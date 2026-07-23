// 品牌皮肤（复合 skins）
// 真实用途：一键应用一套品牌配色（海洋蓝主色 + 大圆角），亮/暗模式分别配。
// 装载后到 /plugins 页选择「海洋蓝」应用。
// 装载：await DeepTutor.plugins.load('https://你的域/brand_skin.js')
(function () {
  var DT = window.DeepTutor;
  if (!DT) return console.warn("[brand_skin] DeepTutor API 不可用");

  DT.skins.register({
    name: "brand.ocean",
    label: "海洋蓝",
    tokens: {
      "--dt-color-primary": "#0ea5e9",
      "--dt-card-radius": "1rem",
      "--dt-bg": "#f0f9ff",
    },
    darkTokens: {
      "--dt-color-primary": "#38bdf8",
      "--dt-bg": "#0c1e2e",
    },
  });

  // 注册即不自动 apply——用户在 /plugins 页或经 skins.apply('brand.ocean') 选择，
  // 避免插件强制改宿主外观（尊重用户当前皮肤选择）。
})();
