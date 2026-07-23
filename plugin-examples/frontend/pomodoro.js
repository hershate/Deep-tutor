// 番茄钟侧栏小组件（复合 widgets + ui.toast + events + api）
// 真实用途：学习时在侧栏放一个番茄钟，完成自动提示并可打卡到学习日志。
// 装载：await DeepTutor.plugins.load('https://你的域/pomodoro.js')
(function () {
  var DT = window.DeepTutor;
  if (!DT) return console.warn("[pomodoro] DeepTutor API 不可用");
  var h = DT.React.createElement;
  var useState = DT.React.useState;
  var useEffect = DT.React.useEffect;
  var useRef = DT.React.useRef;

  function Pomodoro() {
    var state = useState(25 * 60);
    var sec = state[0];
    var setSec = state[1];
    var runState = useState(false);
    var running = runState[0];
    var setRunning = runState[1];
    var timerRef = useRef(null);

    useEffect(function () {
      if (!running) return;
      timerRef.current = setInterval(function () {
        setSec(function (s) {
          if (s <= 1) {
            clearInterval(timerRef.current);
            setRunning(false);
            DT.ui.toast.success("🍅 番茄完成！休息一下");
            // 打卡到学习日志（best-effort，失败静默不打断）
            DT.api
              .request("/progress/logs", {
                method: "POST",
                body: { minutes: 25, note: "番茄钟插件自动打卡" },
              })
              .catch(function () {});
            return 0;
          }
          return s - 1;
        });
      }, 1000);
      return function () {
        clearInterval(timerRef.current);
      };
    }, [running]);

    var mm = String(Math.floor(sec / 60)).padStart(2, "0");
    var ss = String(sec % 60).padStart(2, "0");
    return h(
      "div",
      { style: { padding: "8px 12px", fontVariantNumeric: "tabular-nums", fontSize: 14 } },
      h("span", null, "🍅 " + mm + ":" + ss),
      h(
        "button",
        {
          onClick: function () {
            setRunning(function (r) {
              return !r;
            });
          },
          style: { marginLeft: 8 },
        },
        running ? "暂停" : "开始"
      )
    );
  }

  // 能力发现：旧宿主无 sidebar.footer 插槽则跳过（前向兼容）
  if (DT.widgets.slots().indexOf("sidebar.footer") !== -1) {
    DT.widgets.register({
      id: "pomodoro.timer",
      slot: "sidebar.footer",
      order: 100,
      component: Pomodoro,
    });
  }
  // 监听皮肤切换（复合 events）
  DT.events.on("skinchange", function (e) {
    console.log("[pomodoro] 皮肤切换:", e && e.name);
  });
})();
