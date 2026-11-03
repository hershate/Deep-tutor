use super::tests::{ipc, setup};
use super::*;
use crate::backend::{learning::LearningState, materials, providers::ProviderState};
use serde_json::json;

#[test]
fn workspaces_files_and_quota_failures_are_isolated_by_user() {
    let (app, security) = setup();
    ipc(&app, "get_security_overview", json!({})).unwrap();
    ipc(
        &app,
        "create_local_user",
        json!({"name":"第二位用户","role":"成员"}),
    )
    .unwrap();
    let materials = materials::MaterialState::new(&security.database_path).unwrap();
    let providers = ProviderState::for_tests_at(&security.database_path);
    let learning = LearningState::new(&security.database_path, &providers, &materials);
    let (first_path, first_original) = with_user(1, || {
        let store = learning.store().unwrap();
        store.replace_collection("notes", 0, &json!([{"id":1,"title":"第一位笔记","excerpt":"一","content":"第一位内容","tag":"私有","updated":"刚刚"}])).unwrap();
        let material = materials::import_text(
            app.handle(),
            &materials,
            "共同标题".into(),
            "共同材料内容".into(),
        )
        .unwrap()
        .material;
        (
            learning.workspace_path(),
            learning
                .workspace_path()
                .parent()
                .unwrap()
                .join("materials/originals")
                .join(format!("{}.txt", material.sha256)),
        )
    });
    let (second_path, second_original) = with_user(2, || {
        assert!(learning
            .store()
            .unwrap()
            .collection("notes")
            .unwrap()
            .value
            .as_array()
            .unwrap()
            .is_empty());
        let store = learning.store().unwrap();
        store.replace_collection("notes", 0, &json!([{"id":1,"title":"第二位笔记","excerpt":"二","content":"第二位内容","tag":"私有","updated":"刚刚"}])).unwrap();
        let material = materials::import_text(
            app.handle(),
            &materials,
            "共同标题".into(),
            "共同材料内容".into(),
        )
        .unwrap()
        .material;
        (
            learning.workspace_path(),
            learning
                .workspace_path()
                .parent()
                .unwrap()
                .join("materials/originals")
                .join(format!("{}.txt", material.sha256)),
        )
    });
    assert_ne!(first_path, second_path);
    assert_ne!(first_original, second_original);
    assert!(first_original.is_file());
    assert!(second_original.is_file());
    assert!(first_path.is_file() && second_path.is_file());
    with_user(1, || {
        let store = learning.store().unwrap();
        assert_eq!(
            store.collection("notes").unwrap().value[0]["title"],
            "第一位笔记"
        );
        assert_eq!(materials::list(&materials).unwrap().len(), 1);
        assert!(materials::detail(&materials, 999).is_err());
    });
    with_user(2, || {
        let store = learning.store().unwrap();
        assert_eq!(
            store.collection("notes").unwrap().value[0]["title"],
            "第二位笔记"
        );
        assert_eq!(materials::list(&materials).unwrap().len(), 1);
    });
    assert_eq!(principal_id(), None);
    assert!(security
        .store()
        .unwrap()
        .collection("notes")
        .unwrap()
        .value
        .as_array()
        .unwrap()
        .is_empty());

    set_quota(app.handle(), &security, 1, "notes".into(), 1).unwrap();
    with_user(1, || {
        let error = learning.store().unwrap().replace_collection("notes", 1, &json!([
            {"id":1,"title":"第一位笔记","excerpt":"一","content":"第一位内容","tag":"私有","updated":"刚刚"},
            {"id":2,"title":"超限笔记","excerpt":"不写入","content":"不写入","tag":"私有","updated":"刚刚"},
        ])).unwrap_err();
        assert_eq!(error.code, "quota_exceeded");
        assert_eq!(
            learning
                .store()
                .unwrap()
                .collection("notes")
                .unwrap()
                .value
                .as_array()
                .unwrap()
                .len(),
            1
        );
    });
    with_user(2, || {
        let store = learning.store().unwrap();
        store.replace_collection("notes", 1, &json!([
            {"id":1,"title":"第二位笔记","excerpt":"二","content":"第二位内容","tag":"私有","updated":"刚刚"},
            {"id":2,"title":"第二位新增","excerpt":"允许","content":"允许","tag":"私有","updated":"刚刚"},
        ])).unwrap();
        assert_eq!(
            store
                .collection("notes")
                .unwrap()
                .value
                .as_array()
                .unwrap()
                .len(),
            2
        );
    });
}
