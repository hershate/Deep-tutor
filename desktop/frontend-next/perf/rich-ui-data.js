export const scenarios = {
  map: { spec_version: 1, body: [{ kind: "map", key: "itinerary", label: "东京 · 三日城市漫游", field: { type: "multichoice", required: true, default: ["asakusa", "ueno", "meiji", "shibuya", "garden"], options: [
    { value: "asakusa", label: "浅草寺", group: "第 1 天", description: "从雷门出发，沿仲见世街步行探索浅草。", location: { latitude: 35.7148, longitude: 139.7967 }, facts: [{ label: "建议停留", value: "1.5 小时" }, { label: "区域", value: "浅草" }] },
    { value: "ueno", label: "上野公园", group: "第 1 天", description: "公园散步与博物馆，可根据天气调整。", location: { latitude: 35.7146, longitude: 139.7732 }, facts: [{ label: "建议停留", value: "2 小时" }] },
    { value: "meiji", label: "明治神宫", group: "第 2 天", description: "从城市街区走进安静的林荫参道。", location: { latitude: 35.6764, longitude: 139.6993 }, facts: [{ label: "建议停留", value: "1 小时" }] },
    { value: "shibuya", label: "涩谷十字路口", group: "第 2 天", description: "傍晚感受街区节奏，附近安排晚餐。", location: { latitude: 35.6595, longitude: 139.7005 }, facts: [{ label: "建议停留", value: "1.5 小时" }] },
    { value: "garden", label: "新宿御苑", group: "第 3 天", description: "为最后一天保留一段轻松的花园时光。", location: { latitude: 35.6852, longitude: 139.7100 }, facts: [{ label: "建议停留", value: "2 小时" }] },
  ] } }] },
  comparison: { spec_version: 1, body: [{ kind: "comparison", key: "camera", label: "找到适合旅行的相机", field: { type: "multichoice", required: true, default: ["lite"], options: [
    { value: "lite", image: { path: "perf/media/camera-lite.png", alt: "虚构相机示意图" }, label: "Pocket Lite", group: "轻便随身", description: "更轻的行李，随手记录每一次出发。", price: 699, currency: "USD", facts: [{ label: "重量", value: "294 g" }, { label: "传感器", value: "1 英寸" }, { label: "镜头", value: "24–70 mm" }, { label: "防抖", value: "光学防抖" }], preview: "适合城市漫游与日常记录" },
    { value: "travel", image: { path: "perf/media/camera-travel.png", alt: "虚构相机示意图" }, label: "Travel One", group: "均衡之选", description: "在画质与便携之间找到更好的平衡。", price: 1099, currency: "USD", facts: [{ label: "重量", value: "436 g" }, { label: "传感器", value: "APS-C" }, { label: "镜头", value: "可更换" }, { label: "防抖", value: "五轴机身防抖" }], preview: "适合希望保留更多创作空间的旅行者" },
    { value: "pro", image: { path: "perf/media/camera-pro.png", alt: "虚构相机示意图" }, label: "Horizon Pro", group: "专业创作", description: "为风景、人像与低光创作提供更多细节。", price: 1899, currency: "USD", facts: [{ label: "重量", value: "658 g" }, { label: "传感器", value: "全画幅" }, { label: "镜头", value: "可更换" }, { label: "防抖", value: "五轴机身防抖" }], preview: "适合画质优先的创作行程" },
  ] } }] },
  dashboard: { spec_version: 1, body: [{ kind: "dashboard", label: "业务概览 · 过去 12 个月", dataset: { categories: ["1 月", "2 月", "3 月", "4 月", "5 月", "6 月", "7 月", "8 月", "9 月", "10 月", "11 月", "12 月"], series: [
    { id: "revenue", label: "收入", unit: "USD", values: [12400, 14200, 13100, 18600, 21400, 19800, 25300, 27200, 24800, 31200, 34600, 38200] },
    { id: "orders", label: "订单", values: [124, 139, 131, 180, 202, 193, 246, 267, 244, 304, 337, 371] },
    { id: "visitors", label: "访客", values: [2400, 2700, 2600, 3300, 3800, 3600, 4600, 5100, 4700, 5900, 6400, 7100] },
  ] } }] },
};
