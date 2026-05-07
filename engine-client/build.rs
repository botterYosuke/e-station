fn main() -> Result<(), Box<dyn std::error::Error>> {
    // Vendor protoc so builds work on Windows and CI without a system protoc.
    let protoc = protoc_bin_vendored::protoc_bin_path()
        .expect("protoc-bin-vendored should provide a protoc binary");
    // SAFETY: build scripts run single-threaded before any user code.
    unsafe { std::env::set_var("PROTOC", protoc) };

    // Generate Rust gRPC stubs from proto/engine.proto using tonic-build.
    // Output lands in OUT_DIR; include it in lib.rs via tonic::include_proto!.
    tonic_build::configure()
        .build_server(false) // engine-client is the gRPC client; server is in Python
        .compile_protos(&["../proto/engine.proto"], &["../proto"])?;

    println!("cargo:rerun-if-changed=../proto/engine.proto");
    Ok(())
}
