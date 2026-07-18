// 学习路径大纲（plan/06 §四.4 / plan/03 §二 PlanOutline + StageCard）：
// 知识图谱按 parent_id 组可折叠树（默认展开第一层）；阶段卡可折叠（默认展开 Stage 1，其余收起）。
import { useMemo, useState } from 'react'

export interface OutlineNode {
  id: string
  name: string
  difficulty?: number
  must_learn?: boolean
  parent_id?: string | null
}

export interface OutlineStage {
  index: number
  name: string
  goal: string
  difficulty: number
  estimated_weeks: number
  topics?: Array<{ name: string; why?: string; must_learn?: boolean }>
  milestone?: string
}

interface Props {
  stages?: OutlineStage[]
  knowledgeMap?: { nodes?: OutlineNode[]; edges?: Array<{ from: string; to: string; relation?: string }> } | null
}

/** 知识图谱可折叠树节点。 */
function MapNode({ node, childrenMap, depth }: {
  node: OutlineNode
  childrenMap: Map<string | null, OutlineNode[]>
  depth: number
}) {
  const kids = childrenMap.get(node.id) ?? []
  const [open, setOpen] = useState(depth === 0) // 默认展开第一层
  return (
    <li>
      <div className="flex items-center gap-1.5 py-0.5" style={{ paddingLeft: depth * 16 }}>
        {kids.length > 0 ? (
          <button
            type="button"
            onClick={() => setOpen((o) => !o)}
            aria-expanded={open}
            aria-label={`${open ? '收起' : '展开'} ${node.name}`}
            className="w-5 shrink-0 text-slate-400 hover:text-slate-600 dark:text-slate-300"
          >
            {open ? '▾' : '▸'}
          </button>
        ) : (
          <span className="w-5 shrink-0 text-center text-slate-300">·</span>
        )}
        <span className="text-sm">
          {node.must_learn && <span className="text-amber-500" title="必学">★ </span>}
          {node.name}
        </span>
        {node.difficulty !== undefined && (
          <span className="text-xs text-slate-400">难度 {node.difficulty}</span>
        )}
      </div>
      {open && kids.length > 0 && (
        <ul>
          {kids.map((k) => (
            <MapNode key={k.id} node={k} childrenMap={childrenMap} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  )
}

/** 单阶段卡（可折叠）。 */
function StageCard({ stage: s, defaultOpen }: { stage: OutlineStage; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="rounded-lg border bg-white dark:border-slate-700 dark:bg-slate-800">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-center justify-between rounded-lg p-4 text-left hover:bg-slate-50 dark:hover:bg-slate-700"
      >
        <h4 className="font-medium">
          <span className="mr-1 text-slate-400">{open ? '▾' : '▸'}</span>
          {s.index + 1}. {s.name}
        </h4>
        <span className="text-xs text-slate-400">难度 {s.difficulty} / {s.estimated_weeks} 周</span>
      </button>
      {open && (
        <div className="border-t px-4 pb-4 pt-2">
          <p className="mt-1 text-sm text-slate-600 dark:text-slate-300">目标: {s.goal}</p>
          {s.topics && s.topics.length > 0 && (
            <ul className="mt-2 text-sm">
              {s.topics.map((t, i) => (
                <li key={i}>{t.must_learn ? '★ ' : '· '}{t.name}{t.why ? `（${t.why}）` : ''}</li>
              ))}
            </ul>
          )}
          {s.milestone && <p className="mt-2 text-sm text-slate-500 dark:text-slate-400">里程碑: {s.milestone}</p>}
        </div>
      )}
    </div>
  )
}

export default function PlanOutline({ stages, knowledgeMap }: Props) {
  // parent_id -> 子节点；根 = parent_id 为空或指向不存在节点
  const { roots, childrenMap } = useMemo(() => {
    const nodes = knowledgeMap?.nodes ?? []
    const ids = new Set(nodes.map((n) => n.id))
    const cmap = new Map<string | null, OutlineNode[]>()
    const rootList: OutlineNode[] = []
    for (const n of nodes) {
      const pid = n.parent_id && ids.has(n.parent_id) ? n.parent_id : null
      if (pid === null) rootList.push(n)
      else cmap.set(pid, [...(cmap.get(pid) ?? []), n])
    }
    return { roots: rootList, childrenMap: cmap }
  }, [knowledgeMap])

  return (
    <div className="space-y-4">
      {stages && stages.length > 0 && (
        <section className="space-y-3" aria-label="学习阶段">
          {stages.map((s) => (
            <StageCard key={s.index} stage={s} defaultOpen={s.index === 0} />
          ))}
        </section>
      )}
      {roots.length > 0 && (
        <section className="rounded-lg border bg-white dark:border-slate-700 dark:bg-slate-800 p-4" aria-label="知识图谱">
          <h4 className="mb-2 font-semibold">知识图谱</h4>
          <ul>
            {roots.map((n) => (
              <MapNode key={n.id} node={n} childrenMap={childrenMap} depth={0} />
            ))}
          </ul>
        </section>
      )}
      {(!stages || stages.length === 0) && roots.length === 0 && (
        <p className="text-sm text-slate-500 dark:text-slate-400">暂无学习路径数据。</p>
      )}
    </div>
  )
}
