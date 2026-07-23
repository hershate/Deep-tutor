// 插件页面 + SPA 导航（复合 pages + router）——「用 API 完全替换前端」使能项
// 真实用途：注册一个完整的「速读卡」插件页面（/p/reader）+ 侧栏导航，演示插件构建整套页面
// 并在页面间 SPA 导航（不整页刷新）。
// 装载：await DeepTutor.plugins.load('https://你的域/reader_app.js')
(function () {
  var DT = window.DeepTutor;
  if (!DT) return console.warn("[reader_app] DeepTutor API 不可用");
  var h = DT.React.createElement;

  function ReaderPage() {
    // 查询参数读取无需门面（与宿主同窗口，原生 location.search 可用）
    var params = new URLSearchParams(window.location.search);
    var planId = params.get("planId");
    return h(
      "div",
      { style: { padding: 24, maxWidth: 720, margin: "0 auto" } },
      h("h2", null, "📖 速读卡"),
      h(
        "p",
        null,
        planId
          ? "正在速读方案 " + planId + "（演示插件页面 + 查询参数）"
          : "选择一个方案开始速读（演示插件整页）"
      ),
      h(
        "button",
        {
          onClick: function () {
            DT.router.navigate("/plans"); // SPA 导航回方案列表（不整页刷新）
          },
          style: { marginRight: 8 },
        },
        "← 去方案列表"
      ),
      h(
        "button",
        {
          onClick: function () {
            DT.router.navigate("/p/reader?planId=42", { replace: true }); // 重定向语义
          },
        },
        "模拟选方案 42"
      )
    );
  }

  // 注册完整页面 + 侧栏导航项（/p/reader，零内置路由碰撞）
  if (DT.pages && DT.pages.register) {
    DT.pages.register({
      path: "reader",
      title: "速读卡",
      component: ReaderPage,
      nav: { label: "📖 速读卡", icon: "📖", order: 120 },
    });
  }
})();
