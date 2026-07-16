export default function Home() {
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold">开始生成你的学习方案</h2>
      <p className="text-slate-600 leading-relaxed">
        上传资料、粘贴文本、给出网页链接，或直接描述「我想学 X」。
        系统将自动生成完整学习方案（大纲 + 路径 + 时间 + 资源 + 校验）。
      </p>
      <div className="rounded-lg border bg-white p-6 text-sm text-slate-500">
        前端骨架已就绪。生成流程页面（上传 / 澄清 / 生成进度 / 方案详情 / 设置）将在 step 7 落地。
      </div>
    </div>
  )
}
