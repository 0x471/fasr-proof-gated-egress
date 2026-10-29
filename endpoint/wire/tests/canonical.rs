use pge_wire::*;

fn next(s: &mut u64) -> u64 {
    *s ^= *s << 13;
    *s ^= *s >> 7;
    *s ^= *s << 17;
    *s
}

#[test]
fn refused_or_exactly_canonical_for_seeded_random_and_mutated_inputs() {
    let w = Wire::new(4, 3).unwrap();
    let bases = [
        Message::Request(7, vec![0.5, -0., f64::from_bits(1), 1.]),
        Message::Forward(8, vec![1.; 4]),
        Message::Answer(9, vec![[0; 32]; 3], vec![42; 64]),
        Message::Reply(10, Reply::Ok(vec![0., -1., 2.])),
        Message::Reply(11, Reply::Unavailable),
    ];
    let mut rng = 20261024;
    for kind in 1..=4 {
        let mut accepted = 0;
        for i in 0..15000 {
            let mut raw = if i < 3000 {
                let n = (next(&mut rng) % 200) as usize;
                (0..n).map(|_| next(&mut rng) as u8).collect()
            } else {
                w.encode(&bases[(next(&mut rng) % 5) as usize]).unwrap()
            };
            for _ in 0..(next(&mut rng) % 4) {
                let pos = (next(&mut rng) as usize) % (raw.len() + 1);
                match next(&mut rng) % 3 {
                    0 if pos < raw.len() => raw[pos] ^= 1 << (next(&mut rng) % 8),
                    1 => raw.insert(pos, next(&mut rng) as u8),
                    _ if pos < raw.len() => {
                        raw.remove(pos);
                    }
                    _ => (),
                }
            }
            if let Ok(message) = w.parse(kind, &raw) {
                accepted += 1;
                assert_eq!(w.encode(&message).unwrap(), raw);
            }
        }
        assert!(accepted > 0);
    }
}

#[test]
fn size_type_counter_and_float_boundaries_are_enforced() {
    let w = Wire::new(4, 3).unwrap();
    let raw = w.encode(&Message::Request(1, vec![0.; 4])).unwrap();
    for n in 0..raw.len() {
        assert!(w.parse(1, &raw[..n]).is_err());
    }
    let mut long = raw.clone();
    long.push(0);
    assert!(w.parse(1, &long).is_err());
    let mut counter = raw.clone();
    counter[1..9].copy_from_slice(&MAX_ID.to_be_bytes());
    assert!(w.parse(1, &counter).is_err());
    for kind in [2, 3, 4, 255] {
        assert!(w.parse(kind, &raw).is_err());
    }
    for bits in [
        f64::NAN.to_bits(),
        f64::INFINITY.to_bits(),
        f64::NEG_INFINITY.to_bits(),
        0x7ff0000000000001,
    ] {
        let mut bad = raw.clone();
        bad[9..17].copy_from_slice(&bits.to_be_bytes());
        assert!(w.parse(1, &bad).is_err());
    }
    assert!(Wire::new(usize::MAX, 3).is_err());
    assert!(frame_len([0; 4], 41).is_err());
    assert!(frame_len(42u32.to_be_bytes(), 41).is_err());
    assert_eq!(frame_len(41u32.to_be_bytes(), 41).unwrap(), 41);
}

#[test]
fn error_bodies_and_proof_tails_have_no_alternate_forms() {
    let w = Wire::new(4, 3).unwrap();
    let mut raw = w.encode(&Message::Reply(1, Reply::Unavailable)).unwrap();
    raw[10] = 1;
    assert!(w.parse(4, &raw).is_err());
    raw[10] = 0;
    raw[9] = 3;
    assert!(w.parse(4, &raw).is_err());
    let raw = w
        .encode(&Message::Answer(1, vec![[0; 32]; 3], vec![0; 65536]))
        .unwrap();
    assert!(w.parse(3, &raw).is_ok());
    assert!(w.parse(3, &raw[..105]).is_err());
    let mut too_long = raw;
    too_long.push(0);
    assert!(w.parse(3, &too_long).is_err());
}
