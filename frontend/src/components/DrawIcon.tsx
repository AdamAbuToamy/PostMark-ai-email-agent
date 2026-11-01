import { motion, useReducedMotion } from 'motion/react'
import {
  LayoutGrid, Inbox, ReceiptText, BriefcaseBusiness,
  GraduationCap, Megaphone, Settings2, Mail,
  type LucideIcon,
} from 'lucide-react'

type Props = {
  icon: LucideIcon
  size?: number
  delay?: number
  className?: string
}

type Shape = {
  type: 'path' | 'rect' | 'circle' | 'line'
  d?: string
  x?: number
  y?: number
  width?: number
  height?: number
  cx?: number
  cy?: number
  r?: number
  x1?: number
  y1?: number
  x2?: number
  y2?: number
  rx?: number
}

const drawings = new Map<LucideIcon, Shape[]>([
  [Mail, [
    { type: 'rect', x: 3, y: 5, width: 18, height: 14, rx: 2 },
    { type: 'path', d: 'm3 7 9 6 9-6' },
  ]],
  [Inbox, [
    { type: 'path', d: 'M4 4h16l2 11v5H2v-5L4 4Z' },
    { type: 'path', d: 'M2 15h6l2 3h4l2-3h6' },
  ]],
  [LayoutGrid, [
    { type: 'rect', x: 3, y: 3, width: 7, height: 7, rx: 1 },
    { type: 'rect', x: 14, y: 3, width: 7, height: 7, rx: 1 },
    { type: 'rect', x: 3, y: 14, width: 7, height: 7, rx: 1 },
    { type: 'rect', x: 14, y: 14, width: 7, height: 7, rx: 1 },
  ]],
  [ReceiptText, [
    { type: 'path', d: 'M4 3v18l3-2 3 2 3-2 3 2 4-2V3H4Z' },
    { type: 'path', d: 'M8 8h8M8 12h8M8 16h4' },
  ]],
  [BriefcaseBusiness, [
    { type: 'rect', x: 2, y: 7, width: 20, height: 14, rx: 2 },
    { type: 'path', d: 'M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M2 12c5 4 15 4 20 0M12 13v3' },
  ]],
  [GraduationCap, [
    { type: 'path', d: 'm2 10 10-5 10 5-10 5-10-5Z' },
    { type: 'path', d: 'M6 12v5c4 3 8 3 12 0v-5M22 10v7' },
  ]],
  [Megaphone, [
    { type: 'path', d: 'M3 11v3a2 2 0 0 0 2 2h3l3 5h3l-3-6 9-4V5L8 9H5a2 2 0 0 0-2 2Z' },
    { type: 'path', d: 'M20 8a3 3 0 0 1 0 6' },
  ]],
  [Settings2, [
    { type: 'path', d: 'M4 7h16M4 17h16' },
    { type: 'circle', cx: 9, cy: 7, r: 2 },
    { type: 'circle', cx: 16, cy: 17, r: 2 },
  ]],
])

export function DrawIcon({
  icon: Icon,
  size = 24,
  className,
}: Props) {
  const reduced = useReducedMotion()
  const shapes = drawings.get(Icon)

  if (!shapes) {
    return <Icon size={size} className={className} />
  }

  return (
    <svg
      className={className}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="butt"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {shapes.map((shape, index) => {
        const common = {
          key: index,
          initial: reduced
    ? { pathLength: 1, opacity: 1 }
    : { pathLength: 0, opacity: 0 },
          animate: { pathLength: 1, opacity: 1 },
          transition: {
            duration: reduced ? 0 : 0.65,
            delay: 0,
            ease: 'easeOut' as const,
          },
        }

        switch (shape.type) {
          case 'rect':
            return (
              <motion.rect
                {...common}
                x={shape.x}
                y={shape.y}
                width={shape.width}
                height={shape.height}
                rx={shape.rx}
              />
            )
          case 'circle':
            return (
              <motion.circle
                {...common}
                cx={shape.cx}
                cy={shape.cy}
                r={shape.r}
              />
            )
          case 'line':
            return (
              <motion.line
                {...common}
                x1={shape.x1}
                y1={shape.y1}
                x2={shape.x2}
                y2={shape.y2}
              />
            )
          case 'path':
            return (
              <motion.path
                {...common}
                d={shape.d}
              />
            )
        }
      })}
    </svg>
  )
}
