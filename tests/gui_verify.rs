// tests/gui_verify.rs
// cargo test verify_empty_state_message -- --ignored --nocapture で実行

use image::DynamicImage;
use std::time::{Duration, Instant};
use xcap::Window;
use base64::Engine as _;

fn wait_for_window(prefix: &str, timeout: Duration) -> anyhow::Result<Window> {
    let deadline = Instant::now() + timeout;
    loop {
        if let Some(w) = Window::all()?
            .into_iter()
            .find(|w| w.title().starts_with(prefix))
        {
            return Ok(w);
        }
        anyhow::ensure!(
            Instant::now() < deadline,
            "ウィンドウ '{}' が {:?} 内に現れなかった",
            prefix,
            timeout
        );
        std::thread::sleep(Duration::from_millis(300));
    }
}

async fn vision_yes_no(img: &DynamicImage, question: &str) -> anyhow::Result<bool> {
    let mut buf = std::io::Cursor::new(Vec::new());
    img.write_to(&mut buf, image::ImageFormat::Png)?;
    let b64 = base64::engine::general_purpose::STANDARD.encode(buf.into_inner());
    let api_key = std::env::var("ANTHROPIC_API_KEY").expect("ANTHROPIC_API_KEY not set");
    let body = serde_json::json!({
        "model": "claude-sonnet-4-6",
        "max_tokens": 16,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64}},
            {"type": "text", "text": format!("Answer only YES or NO. {question}")}
        ]}]
    });
    let resp: serde_json::Value = reqwest::Client::new()
        .post("https://api.anthropic.com/v1/messages")
        .header("x-api-key", &api_key)
        .header("anthropic-version", "2023-06-01")
        .json(&body)
        .send()
        .await?
        .json()
        .await?;
    Ok(resp["content"][0]["text"]
        .as_str()
        .unwrap_or("NO")
        .trim()
        .starts_with("YES"))
}

#[tokio::test]
#[ignore = "requires display + running app + ANTHROPIC_API_KEY"]
async fn verify_empty_state_message() -> anyhow::Result<()> {
    // アプリは手順 1 で起動済みの前提
    let window = wait_for_window("Flowsurface", Duration::from_secs(10))?;
    std::thread::sleep(Duration::from_secs(2)); // レンダリング安定待ち

    let img = DynamicImage::ImageRgba8(window.capture_image()?);
    std::fs::create_dir_all("tests/screenshots")?;
    img.save("tests/screenshots/verify_empty_state.png")?;

    let ok = vision_yes_no(
        &img,
        "Is the Japanese text '銘柄を選択してください' or '立花へのログインが必要です' visible somewhere in the panel area of this application window?",
    )
    .await?;
    assert!(
        ok,
        "未選択/未ログイン時に正しいメッセージが表示されるはず — スクショ: tests/screenshots/verify_empty_state.png"
    );

    Ok(())
}
