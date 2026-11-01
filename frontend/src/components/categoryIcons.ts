import {
  MessageCircle,
  Shield,
  Plane,
  ReceiptText,
  BriefcaseBusiness,
  GraduationCap,
  Megaphone,
  FolderOpen,
  Bell,
  HeartPulse,
  FileText,
  CalendarDays,
  Wallet,
  Package,
  BookOpen,
  Building2,
} from 'lucide-react'

export const categoryIcons = {
  'message-circle': MessageCircle,
  shield: Shield,
  plane: Plane,
  receipt: ReceiptText,
  briefcase: BriefcaseBusiness,
  'graduation-cap': GraduationCap,
  megaphone: Megaphone,
  'folder-open': FolderOpen,
  bell: Bell,
  'heart-pulse': HeartPulse,
  'file-text': FileText,
  'calendar-days': CalendarDays,
  wallet: Wallet,
  package: Package,
  'book-open': BookOpen,
  'building-2': Building2,
} as const

export function getCategoryIcon(key: string) {
  if (Object.prototype.hasOwnProperty.call(categoryIcons, key)) {
    return categoryIcons[key as keyof typeof categoryIcons]
  }

  return FolderOpen
}
