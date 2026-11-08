#[cfg(desktop)]
use std::fs::{self, File};
#[cfg(desktop)]
use std::process::{Child, Command, Stdio};
#[cfg(desktop)]
use std::sync::Mutex;
#[cfg(desktop)]
use tauri::Manager;

/// The bundled Python backend (release builds only — in dev, scripts/dev.mjs runs it). Phones connect to the Mac's.
#[cfg(desktop)]
struct Backend(Mutex<Option<Child>>);

#[cfg(desktop)]
fn start_backend(app: &tauri::App) -> Result<Child, Box<dyn std::error::Error>> {
  let exe = app.path().resource_dir()?.join("backend").join(if cfg!(windows) { "jarvis-backend.exe" } else { "jarvis-backend" });
  let home = app.path().app_data_dir()?; // ~/Library/Application Support/com.jarvis.station · %APPDATA%\com.jarvis.station
  fs::create_dir_all(home.join("logs"))?;
  let out = File::create(home.join("logs").join("backend-console.log"))?;
  let mut cmd = Command::new(&exe);
  cmd.env("JARVIS_HOME", &home).current_dir(&home).stdin(Stdio::null()).stdout(out.try_clone()?).stderr(out);
  #[cfg(windows)]
  {
    use std::os::windows::process::CommandExt;
    cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console window next to the app
  }
  Ok(cmd.spawn()?)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
  tauri::Builder::default()
    .setup(|app| {
      if cfg!(debug_assertions) {
        app.handle().plugin(tauri_plugin_log::Builder::default().level(log::LevelFilter::Info).build())?;
      }
      #[cfg(desktop)]
      if !cfg!(debug_assertions) {
        // a failed start is shown in the UI as "backend offline"; details land in logs/backend-console.log
        let child = start_backend(app).map_err(|e| eprintln!("JARVIS backend failed to start: {e}")).ok();
        app.manage(Backend(Mutex::new(child)));
      }
      Ok(())
    })
    .build(tauri::generate_context!())
    .expect("error while building tauri application")
    .run(|_app, _event| {
      #[cfg(desktop)]
      if let tauri::RunEvent::Exit = _event {
        if let Some(b) = _app.try_state::<Backend>() {
          if let Some(mut child) = b.0.lock().unwrap().take() {
            let _ = child.kill();
            let _ = child.wait();
          }
        }
      }
    });
}
