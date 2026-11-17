import { forwardRef } from 'react'
import type { SVGProps } from 'react'
import {
  Accessibility as AccessibilityBase,
  Activity as ActivityBase,
  AlertTriangle as AlertTriangleBase,
  ArrowDown as ArrowDownBase,
  ArrowDownToLine as ArrowDownToLineBase,
  ArrowLeft as ArrowLeftBase,
  ArrowRight as ArrowRightBase,
  ArrowUp as ArrowUpBase,
  Ban as BanBase,
  Bell as BellBase,
  Blocks as BlocksBase,
  BookMarked as BookMarkedBase,
  BookOpen as BookOpenBase,
  BookOpenCheck as BookOpenCheckBase,
  Bot as BotBase,
  Brain as BrainBase,
  CalendarClock as CalendarClockBase,
  CalendarDays as CalendarDaysBase,
  CalendarRange as CalendarRangeBase,
  Check as CheckBase,
  CheckCircle2 as CheckCircle2Base,
  ChevronDown as ChevronDownBase,
  ChevronLeft as ChevronLeftBase,
  ChevronRight as ChevronRightBase,
  Circle as CircleBase,
  CircleAlert as CircleAlertBase,
  CircleDashed as CircleDashedBase,
  CircleHelp as CircleHelpBase,
  Clipboard as ClipboardBase,
  Clock3 as Clock3Base,
  Cloud as CloudBase,
  CloudCog as CloudCogBase,
  CloudUpload as CloudUploadBase,
  Code2 as Code2Base,
  Command as CommandBase,
  Copy as CopyBase,
  Database as DatabaseBase,
  Download as DownloadBase,
  Eye as EyeBase,
  EyeOff as EyeOffBase,
  File as FileBase,
  FileArchive as FileArchiveBase,
  FileAudio as FileAudioBase,
  FileCheck2 as FileCheck2Base,
  FileCode2 as FileCode2Base,
  FileJson as FileJsonBase,
  FileQuestion as FileQuestionBase,
  FileText as FileTextBase,
  FileUp as FileUpBase,
  Film as FilmBase,
  Flame as FlameBase,
  Folder as FolderBase,
  FolderKanban as FolderKanbanBase,
  FolderOpen as FolderOpenBase,
  Gauge as GaugeBase,
  GitBranch as GitBranchBase,
  Globe2 as Globe2Base,
  HardDriveDownload as HardDriveDownloadBase,
  Hash as HashBase,
  Heart as HeartBase,
  Highlighter as HighlighterBase,
  History as HistoryBase,
  Home as HomeBase,
  Inbox as InboxBase,
  KeyRound as KeyRoundBase,
  Keyboard as KeyboardBase,
  Layers3 as Layers3Base,
  LibraryBig as LibraryBigBase,
  Lightbulb as LightbulbBase,
  Link2 as Link2Base,
  List as ListBase,
  ListChecks as ListChecksBase,
  ListFilter as ListFilterBase,
  ListTree as ListTreeBase,
  LoaderCircle as LoaderCircleBase,
  LockKeyhole as LockKeyholeBase,
  Map as MapBase,
  Maximize2 as Maximize2Base,
  Menu as MenuBase,
  MessageCircle as MessageCircleBase,
  MessageCircleQuestion as MessageCircleQuestionBase,
  MessageSquareText as MessageSquareTextBase,
  Minus as MinusBase,
  Moon as MoonBase,
  MoreHorizontal as MoreHorizontalBase,
  Network as NetworkBase,
  NotebookPen as NotebookPenBase,
  Pause as PauseBase,
  PenLine as PenLineBase,
  Play as PlayBase,
  Plug as PlugBase,
  Plus as PlusBase,
  Presentation as PresentationBase,
  Quote as QuoteBase,
  RefreshCw as RefreshCwBase,
  Rocket as RocketBase,
  RotateCcw as RotateCcwBase,
  Route as RouteBase,
  Search as SearchBase,
  Send as SendBase,
  Server as ServerBase,
  Settings as SettingsBase,
  Settings2 as Settings2Base,
  Share2 as Share2Base,
  ShieldCheck as ShieldCheckBase,
  SlidersHorizontal as SlidersHorizontalBase,
  Sparkles as SparklesBase,
  Square as SquareBase,
  SquareArrowOutUpRight as SquareArrowOutUpRightBase,
  Sun as SunBase,
  Table2 as Table2Base,
  Tag as TagBase,
  Target as TargetBase,
  Timer as TimerBase,
  TimerReset as TimerResetBase,
  Trash2 as Trash2Base,
  TrendingUp as TrendingUpBase,
  Trophy as TrophyBase,
  Upload as UploadBase,
  UserRound as UserRoundBase,
  Users as UsersBase,
  Video as VideoBase,
  Wrench as WrenchBase,
  X as XBase,
  XCircle as XCircleBase,
  Zap as ZapBase,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

export type { LucideIcon } from 'lucide-react'

type IconProps = Omit<SVGProps<SVGSVGElement>, 'ref'> & { size?: string | number; absoluteStrokeWidth?: boolean }

// P-164：lucide-react 0.468.0 的 defaultAttributes 只有 xmlns/size/viewBox/
// 描边属性，既无 aria-hidden 也无 role——每个图标都以「无可访问名称的图形
// 节点」进入无障碍树，读屏逐个多播报一次。项目自身手写的内联 SVG（品牌
// 标记、进度环）都显式声明 aria-hidden="true"，两套约定不一致。
// 应用侧统一在包装层补 aria-hidden，不改动依赖包；调用方需要图标承载
// 语义（如「正确选项」标记）时显式传 aria-hidden={false} 退出装饰模式。
//
// P-164 返工：原实现是 `<Icon aria-hidden="true" {...props} />`——props 在后，
// 调用方写 aria-hidden={undefined}（JSX 常见写法）会把默认值覆盖成
// undefined，图标重新进入无障碍树且没有任何告警。改为把 aria-hidden 从
// props 中摘出后显式合并：只有显式传 false / 'false' 才退出装饰模式，
// undefined 与 null 落回默认 true。合并用 ?? 而非 ||——|| 会把显式的
// false 变成 true，等于关掉退出装饰模式的开关。
function decorative(icon: LucideIcon): LucideIcon {
  const Icon = icon
  const Wrapped = forwardRef<SVGSVGElement, IconProps>(
    ({ ['aria-hidden']: ariaHidden, ...rest }, ref) => (
      <Icon ref={ref} {...rest} aria-hidden={ariaHidden ?? true} />
    ),
  )
  Wrapped.displayName = 'DecorativeIcon'
  return Wrapped
}

export const Accessibility = decorative(AccessibilityBase)
export const Activity = decorative(ActivityBase)
export const AlertTriangle = decorative(AlertTriangleBase)
export const ArrowDown = decorative(ArrowDownBase)
export const ArrowDownToLine = decorative(ArrowDownToLineBase)
export const ArrowLeft = decorative(ArrowLeftBase)
export const ArrowRight = decorative(ArrowRightBase)
export const ArrowUp = decorative(ArrowUpBase)
export const Ban = decorative(BanBase)
export const Bell = decorative(BellBase)
export const Blocks = decorative(BlocksBase)
export const BookMarked = decorative(BookMarkedBase)
export const BookOpen = decorative(BookOpenBase)
export const BookOpenCheck = decorative(BookOpenCheckBase)
export const Bot = decorative(BotBase)
export const Brain = decorative(BrainBase)
export const CalendarClock = decorative(CalendarClockBase)
export const CalendarDays = decorative(CalendarDaysBase)
export const CalendarRange = decorative(CalendarRangeBase)
export const Check = decorative(CheckBase)
export const CheckCircle2 = decorative(CheckCircle2Base)
export const ChevronDown = decorative(ChevronDownBase)
export const ChevronLeft = decorative(ChevronLeftBase)
export const ChevronRight = decorative(ChevronRightBase)
export const Circle = decorative(CircleBase)
export const CircleAlert = decorative(CircleAlertBase)
export const CircleDashed = decorative(CircleDashedBase)
export const CircleHelp = decorative(CircleHelpBase)
export const Clipboard = decorative(ClipboardBase)
export const Clock3 = decorative(Clock3Base)
export const Cloud = decorative(CloudBase)
export const CloudCog = decorative(CloudCogBase)
export const CloudUpload = decorative(CloudUploadBase)
export const Code2 = decorative(Code2Base)
export const Command = decorative(CommandBase)
export const Copy = decorative(CopyBase)
export const Database = decorative(DatabaseBase)
export const Download = decorative(DownloadBase)
export const Eye = decorative(EyeBase)
export const EyeOff = decorative(EyeOffBase)
export const File = decorative(FileBase)
export const FileArchive = decorative(FileArchiveBase)
export const FileAudio = decorative(FileAudioBase)
export const FileCheck2 = decorative(FileCheck2Base)
export const FileCode2 = decorative(FileCode2Base)
export const FileJson = decorative(FileJsonBase)
export const FileQuestion = decorative(FileQuestionBase)
export const FileText = decorative(FileTextBase)
export const FileUp = decorative(FileUpBase)
export const Film = decorative(FilmBase)
export const Flame = decorative(FlameBase)
export const Folder = decorative(FolderBase)
export const FolderKanban = decorative(FolderKanbanBase)
export const FolderOpen = decorative(FolderOpenBase)
export const Gauge = decorative(GaugeBase)
export const GitBranch = decorative(GitBranchBase)
export const Globe2 = decorative(Globe2Base)
export const HardDriveDownload = decorative(HardDriveDownloadBase)
export const Hash = decorative(HashBase)
export const Heart = decorative(HeartBase)
export const Highlighter = decorative(HighlighterBase)
export const History = decorative(HistoryBase)
export const Home = decorative(HomeBase)
export const Inbox = decorative(InboxBase)
export const KeyRound = decorative(KeyRoundBase)
export const Keyboard = decorative(KeyboardBase)
export const Layers3 = decorative(Layers3Base)
export const LibraryBig = decorative(LibraryBigBase)
export const Lightbulb = decorative(LightbulbBase)
export const Link2 = decorative(Link2Base)
export const List = decorative(ListBase)
export const ListChecks = decorative(ListChecksBase)
export const ListFilter = decorative(ListFilterBase)
export const ListTree = decorative(ListTreeBase)
export const LoaderCircle = decorative(LoaderCircleBase)
export const LockKeyhole = decorative(LockKeyholeBase)
export const Map = decorative(MapBase)
export const Maximize2 = decorative(Maximize2Base)
export const Menu = decorative(MenuBase)
export const MessageCircle = decorative(MessageCircleBase)
export const MessageCircleQuestion = decorative(MessageCircleQuestionBase)
export const MessageSquareText = decorative(MessageSquareTextBase)
export const Minus = decorative(MinusBase)
export const Moon = decorative(MoonBase)
export const MoreHorizontal = decorative(MoreHorizontalBase)
export const Network = decorative(NetworkBase)
export const NotebookPen = decorative(NotebookPenBase)
export const Pause = decorative(PauseBase)
export const PenLine = decorative(PenLineBase)
export const Play = decorative(PlayBase)
export const Plug = decorative(PlugBase)
export const Plus = decorative(PlusBase)
export const Presentation = decorative(PresentationBase)
export const Quote = decorative(QuoteBase)
export const RefreshCw = decorative(RefreshCwBase)
export const Rocket = decorative(RocketBase)
export const RotateCcw = decorative(RotateCcwBase)
export const Route = decorative(RouteBase)
export const Search = decorative(SearchBase)
export const Send = decorative(SendBase)
export const Server = decorative(ServerBase)
export const Settings = decorative(SettingsBase)
export const Settings2 = decorative(Settings2Base)
export const Share2 = decorative(Share2Base)
export const ShieldCheck = decorative(ShieldCheckBase)
export const SlidersHorizontal = decorative(SlidersHorizontalBase)
export const Sparkles = decorative(SparklesBase)
export const Square = decorative(SquareBase)
export const SquareArrowOutUpRight = decorative(SquareArrowOutUpRightBase)
export const Sun = decorative(SunBase)
export const Table2 = decorative(Table2Base)
export const Tag = decorative(TagBase)
export const Target = decorative(TargetBase)
export const Timer = decorative(TimerBase)
export const TimerReset = decorative(TimerResetBase)
export const Trash2 = decorative(Trash2Base)
export const TrendingUp = decorative(TrendingUpBase)
export const Trophy = decorative(TrophyBase)
export const Upload = decorative(UploadBase)
export const UserRound = decorative(UserRoundBase)
export const Users = decorative(UsersBase)
export const Video = decorative(VideoBase)
export const Wrench = decorative(WrenchBase)
export const X = decorative(XBase)
export const XCircle = decorative(XCircleBase)
export const Zap = decorative(ZapBase)
