#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;

use backend::{commands, materials, store::Store, BackendState};
use std::sync::Mutex;
use tauri::Manager;

fn main() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let path = app.path().app_data_dir()?.join("deep-tutor.db");
            let store = Store::new(&path)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            let material_state = materials::MaterialState::new(&path)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            app.manage(BackendState {
                store: Mutex::new(store),
                materials: material_state,
            });
            let state = app.state::<BackendState>();
            materials::recover(app.handle(), &state.materials)
                .map_err(|error| -> Box<dyn std::error::Error> { error.into() })?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::bootstrap,
            commands::get_app_state,
            commands::get_collection,
            commands::replace_collection,
            commands::save_draft,
            commands::get_draft,
            commands::delete_draft,
            commands::reset_application,
            commands::export_backup,
            commands::preview_backup,
            commands::import_backup,
            commands::events_since,
            materials::commands::import_material_files,
            materials::commands::list_materials,
            materials::commands::get_material,
            materials::commands::enqueue_material_parse,
            materials::commands::cancel_material_parse,
            materials::commands::delete_material,
            materials::commands::export_material_original,
            materials::commands::export_material_text,
        ])
        .run(tauri::generate_context!())
        .expect("error while running the Deep Tutor application");
}
