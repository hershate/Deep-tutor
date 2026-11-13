# Animation & Transition Reference

> 资产状态：本文件是 `ppt-slide-builder/templates/base.html` 的源码存档说明，不进入安装包，也不是当前应用的可执行能力。

## 一、元素动画

动画类添加到 HTML 元素上。父级 `.slide` 获得 `.active` 时触发动画；每个元素最多使用一个动画类和一个延迟类。

| 类名 | CSS 行为 | 时长 | 适用元素 |
|---|---|---:|---|
| `anim-fade-in` | opacity 0 → 1 | 0.6s | 所有元素 |
| `anim-slide-up` | translateY(30px) → 0 | 0.6s | 所有元素 |
| `anim-slide-down` | translateY(-30px) → 0 | 0.6s | 标题、图片 |
| `anim-slide-left` | translateX(30px) → 0 | 0.6s | 列表、卡片 |
| `anim-slide-right` | translateX(-30px) → 0 | 0.6s | 图片、装饰元素 |
| `anim-scale-in` | scale(0.7) → scale(1) | 0.5s | 图标、图片、行动按钮 |
| `anim-rotate-in` | rotate(-5deg) + scale(0.9) → 无变换 | 0.6s | 装饰元素、图片 |
| `anim-blur-in` | blur(10px) → blur(0) | 0.8s | 背景文字、标题 |
| `anim-flip-in` | perspective(600px) rotateX(-20deg) → 0 | 0.7s | 卡片、区块 |
| `anim-bounce-in` | translateY(40px) → -8px → 2px → 0 | 0.8s | 数字、指标、强调元素 |
| `anim-typewriter` | width 0 → 100%，steps(30)，光标闪烁 | 1.5s | 单行短文本 |
| `anim-highlight` | 背景位置 200% → 0 | 0.8s | 关键词、引用 |

### 延迟类名

| 类名 | 延迟 | 类名 | 延迟 |
|---|---:|---|---:|
| `anim-delay-1` | 0.1s | `anim-delay-6` | 0.6s |
| `anim-delay-2` | 0.2s | `anim-delay-7` | 0.7s |
| `anim-delay-3` | 0.3s | `anim-delay-8` | 0.8s |
| `anim-delay-4` | 0.4s | `anim-delay-9` | 0.9s |
| `anim-delay-5` | 0.5s | `anim-delay-10` | 1.0s |

示例：

```html
<h1 class="anim-fade-in">标题</h1>
<p class="anim-fade-in anim-delay-2">延迟 0.2 秒的正文</p>
<ul>
  <li class="anim-slide-up">第一项</li>
  <li class="anim-slide-up anim-delay-1">第二项</li>
  <li class="anim-slide-up anim-delay-2">第三项</li>
</ul>
```

编辑约定每页动画元素不超过 5 个，延迟增量使用 0.1–0.2 秒；模板不强制执行这些数量规则。`anim-typewriter` 使用 `white-space:nowrap`，只适用于单行短文本。

## 二、页面过渡

过渡类添加到 `.presentation`，每次只使用一种。

| 类名 | 非活动页初始状态 | 时长与行为 |
|---|---|---|
| `trans-fade` | 无位移 | opacity 按全局过渡时长变化 |
| `trans-slide-left` | translateX(30px) | 页面从右侧进入 |
| `trans-slide-right` | translateX(-30px) | 页面从左侧进入 |
| `trans-slide-up` | translateY(30px) | 页面从下侧进入 |
| `trans-zoom` | scale(0.85) | 退场缩小、入场放大 |
| `trans-flip` | perspective + rotateX(10deg) + translateY(20px) | 带透视的旋转过渡 |
| `trans-none` | 无位移 | 仅保留 0.3s opacity 过渡，不是禁用全部动画 |

示例：

```html
<div class="presentation trans-slide-left" id="presentation">
```

更换过渡时只替换 `trans-*` 类，保留 `presentation` 和 `id="presentation"`。一个演示使用同一种页面过渡。

## 三、减弱动态效果

模板在 `prefers-reduced-motion: reduce` 下将动画和过渡时长设为 0.01ms、动画迭代次数设为 1、滚动行为设为 auto，并移除 `.slide` 的 transform。该规则作用于模板全部元素。
