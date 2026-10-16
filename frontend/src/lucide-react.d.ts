declare module 'lucide-react' {
  import type { ComponentType, SVGProps } from 'react'

  type IconProps = SVGProps<SVGSVGElement> & {
    size?: number | string
    absoluteStrokeWidth?: boolean
    color?: string
    strokeWidth?: number | string
  }
  type Icon = ComponentType<IconProps>

  export const ArrowDown: Icon
  export const ArrowRight: Icon
  export const BookOpen: Icon
  export const Check: Icon
  export const ChevronRight: Icon
  export const CircleHelp: Icon
  export const Code2: Icon
  export const Compass: Icon
  export const ExternalLink: Icon
  export const FileCode2: Icon
  export const Github: Icon
  export const GitPullRequest: Icon
  export const LoaderCircle: Icon
  export const Search: Icon
  export const ShieldCheck: Icon
  export const Sparkles: Icon
  export const Star: Icon
  export const Terminal: Icon
}
