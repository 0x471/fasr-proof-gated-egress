use pge_verify::*;
use serde_json::{json,Value};
use std::{fs, path::PathBuf};

fn fixture(name: &str) -> PathBuf { PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures").join(name) }
fn bundle(name: &str) -> Bundle {
    let root = fixture(name);
    let srs = std::env::var("PGE_SRS").expect("set PGE_SRS; integration is mandatory");
    let raw = [fs::read(root.join("settings.json")).unwrap(),fs::read(root.join("vk.key")).unwrap(),fs::read(srs).unwrap()];
    assert_eq!(digest(&raw[2]),"90807800a1c3b248a452e1732c45ee5099f38b737356f5542c0584ec9c3ebb45");
    let pins = Pins([digest(&raw[0]),digest(&raw[1]),digest(&raw[2])]);
    Bundle::from_bytes(raw,pins).unwrap()
}

#[test]
fn real_proofs_accept_and_tampering_rejects_in_memory() {
    for name in ["tiny","multi"] {
        let b = bundle(name);
        let raw = fs::read(fixture(name).join("proof.json")).unwrap();
        let p = canonical_proof(&raw).unwrap();
        let len = p["proof"].as_array().unwrap().len();
        assert_eq!(check_proof(&b,&raw,len),Verdict::Accept);
        for idx in [0,p["instances"][0].as_array().unwrap().len()-1] {
            let mut bad = p.clone(); bad["instances"][0][idx] = json!(hex(&felt(123456)));
            assert_eq!(check_proof(&b,&serde_json::to_vec(&bad).unwrap(),len),Verdict::Reject);
        }
        let mut bad = p.clone(); bad["proof"].as_array_mut().unwrap().push(json!(0));
        assert_eq!(check_proof(&b,&serde_json::to_vec(&bad).unwrap(),len),Verdict::Reject);
        assert_eq!(check_proof(&b,b"garbage",len),Verdict::Reject);
        assert_eq!(check_proof(&b,&raw,len-1),Verdict::Reject);
        let mut bad = p.clone(); bad["proof"][0] = json!(256);
        assert_eq!(check_proof(&b,&serde_json::to_vec(&bad).unwrap(),len),Verdict::Reject);
        let mut stream = Vec::new(); b.write_job(&raw,&mut stream).unwrap();
        let (copy,p) = Bundle::read_job(&mut stream.as_slice(),b.pins.clone()).unwrap();
        assert_eq!(check_proof(&copy,&p,len),Verdict::Accept);
        stream.push(0);
        assert!(Bundle::read_job(&mut stream.as_slice(),b.pins.clone()).is_err());
    }
}

#[test]
fn f32_conversion_rounding_and_limits() {
    for (x,f32,f64) in [(0.5/8192.,1,1),(-0.5/8192.,-1,-1),(2.5/8192.,3,3),((0.5-1e-9)/8192.,1,0)] {
        assert_eq!(quantize(x,13,true).unwrap(),f32);
        assert_eq!(quantize(x,13,false).unwrap(),f64);
    }
    for x in [0.,-0., f32::from_bits(1) as f64, -(f32::from_bits(1) as f64)] { assert_eq!(quantize(x,13,true).unwrap(),0); }
    for x in [f64::NAN,f64::INFINITY,-f64::INFINITY,1e39,2f64.powi(50)] { assert!(quantize(x,13,true).is_err()); }
    assert_eq!(quantize(0.49999999999999994,0,false).unwrap(),0);
}

#[test]
fn multi_tensor_order_signed_decoding_and_request_hash() {
    let b = bundle("multi");
    let p:Value = serde_json::from_slice(&fs::read(fixture("multi").join("proof.json")).unwrap()).unwrap();
    let outputs:Vec<_> = p["instances"][0].as_array().unwrap()[4..].iter().map(|v| parse_hex(v.as_str().unwrap()).unwrap()).collect();
    let (raw,y) = b.spec.bind(&[0.25,-0.5,-0.75,0.125],&outputs,&[0;3072],3072).unwrap();
    assert_eq!(y,[-1.,0.625,-0.1875,-0.0625]);
    assert_eq!(serde_json::from_slice::<Value>(&raw).unwrap()["instances"],p["instances"]);
    assert_ne!(b.spec.quantize(&[-0.75,0.125,0.25,-0.5]).unwrap(),b.spec.quantize(&[0.25,-0.5,-0.75,0.125]).unwrap());
}

#[test]
fn settings_and_pins_are_strict() {
    let raw = fs::read(fixture("tiny").join("settings.json")).unwrap();
    let original:Value = serde_json::from_slice(&raw).unwrap();
    for (key,value) in [("version",json!("23.0.4")),("input_types",json!(["F16"])),("model_input_scales",json!([true])),("model_input_scales",json!([13.0])),("model_output_scales",json!([-1])),("model_instance_shapes",json!([[256,257],[1,3]])),("input_types",json!([])),("model_instance_shapes",json!([[1,4]]))] {
        let mut bad = original.clone(); bad[key] = value;
        assert!(Spec::parse(&serde_json::to_vec(&bad).unwrap()).is_err(),"{key}");
    }
    for key in ["input_visibility","output_visibility","param_visibility"] {
        let mut bad=original.clone(); bad["run_args"][key]=json!("Private");
        assert!(Spec::parse(&serde_json::to_vec(&bad).unwrap()).is_err());
    }
    let mut pins = bundle("tiny").pins.clone(); pins.0[0] = "00".repeat(32);
    assert!(Bundle::from_bytes([raw,vec![],vec![]],pins).is_err());
}

#[test]
fn noncanonical_fields_and_malformed_shapes_are_refused() {
    for s in ["FF".repeat(32),"ff".repeat(32),"00".repeat(31),"0x".to_string()+&"00".repeat(31)] { assert!(parse_hex(&s).is_err()); }
    let q = "010000f093f5e1439170b97948e833285d588181b64550b829a031e1724e6430";
    assert!(parse_hex(q).is_err());
    assert_eq!(parse_hex(&hex(&felt(-1638))).unwrap(),felt(-1638));
    let s = Spec::parse(&fs::read(fixture("tiny").join("settings.json")).unwrap()).unwrap();
    assert!(s.bind(&[0.;4],&[[0;32];2],&[0;3072],3072).is_err());
    assert!(s.bind(&[0.;3],&[[0;32];3],&[0;3072],3072).is_err());
    for raw in [b"[]".as_slice(),b"{\"proof\":[true],\"instances\":[]}",b"{\"proof\":[1.0],\"instances\":[]}"] { assert!(canonical_proof(raw).is_err()); }
}

#[test]
fn domain_checks_input_and_activation_boundaries() {
    let domain = Domain::parse(br#"{"wq":[[1],[0]],"bq":[0],"bound":5}"#).unwrap();
    for n in [[5,5],[-5,-5],[0,0]] { assert!(domain.admits(&n)); }
    for n in [[6,0],[-6,0],[0,6],[0,-6]] { assert!(!domain.admits(&n)); }
    let domain = Domain::parse(br#"{"wq":[[2]],"bq":[1],"bound":5}"#).unwrap();
    assert!(domain.admits(&[2])); assert!(domain.admits(&[-3]));
    assert!(!domain.admits(&[3])); assert!(!domain.admits(&[-4]));
}
