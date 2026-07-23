// 可拖放浮窗笔记（复合 overlay + ui.makeDraggable + ui.toast）
// 真实用途：学习时随手记笔记的浮窗，可自由拖放（位置记忆），内容存本地。
// 装载：await DeepTutor.plugins.load('https://你的域/floating_notes.js')
(function () {
  var DT = window.DeepTutor;
  if (!DT) return console.warn("[floating_notes] DeepTutor API 不可用");
  var h = DT.React.createElement;
  var useEffect = DT.React.useEffect;
  var useRef = DT.React.useRef;
  var useState = DT.React.useState;

  function FloatingNotes() {
    var ref = useRef(null);
    var state = useState(localStorage.getItem("floating_notes_text") || "");
    var text = state[0];
    var setText = state[1];

    useEffect(function () {
      if (!ref.current) return;
      // 一次调用获得拖放 + 视口边界 + 位置持久化（刷新回到上次位置）
      var d = DT.ui.makeDraggable(ref.current, { persistKey: "floating_notes_pos" });
      return function () {
        d.destroy();
      };
    }, []);

    return h(
      "div",
      {
        ref: ref,
        style: {
          width: 220,
          background: "#fff",
          border: "1px solid #e2e8f0",
          borderRadius: "var(--dt-card-radius, 0.75rem)",
          padding: 12,
          boxShadow: "0 4px 12px rgba(0,0,0,.1)",
        },
      },
      h("div", { style: { fontWeight: 600, marginBottom: 6 } }, "📝 浮窗笔记（拖我）"),
      h("textarea", {
        value: text,
        onChange: function (e) {
          var v = e.target.value;
          setText(v);
          localStorage.setItem("floating_notes_text", v);
        },
        style: { width: "100%", height: 60, resize: "vertical" },
      }),
      h(
        "button",
        {
          onClick: function () {
            return DT.ui.toast.success("已保存到本地");
          },
          style: { marginTop: 6 },
        },
        "保存"
      )
    );
  }

  // 能力发现：旧宿主无 overlay 则跳过
  if (DT.overlay.layers().indexOf("content") !== -1) {
    DT.overlay.register({
      id: "floating_notes.panel",
      layer: "content",
      pointerEvents: "auto", // 浮窗需交互（拖拽/输入）
      component: FloatingNotes,
    });
  }
})();
