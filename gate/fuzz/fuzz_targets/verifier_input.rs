#![no_main]
libfuzzer_sys::fuzz_target!(|raw: &[u8]| {
    let _ = pge_verify::canonical_proof(raw);
    let _ = pge_verify::Spec::parse(raw);
    let _ = pge_verify::Domain::parse(raw);
    let pins = pge_verify::Pins(["0".repeat(64), "0".repeat(64), "0".repeat(64)]);
    let _ = pge_verify::Bundle::read_job(&mut raw.as_ref(), pins);
});
