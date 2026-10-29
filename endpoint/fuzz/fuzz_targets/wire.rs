#![no_main]
libfuzzer_sys::fuzz_target!(|raw: &[u8]| {
    let wire = pge_wire::Wire::new(4, 3).unwrap();
    for kind in 1..=4 {
        if let Ok(message) = wire.parse(kind, raw) {
            assert_eq!(wire.encode(&message).unwrap(), raw);
        }
    }
});
