import {
  Bot,
  Box,
  Camera,
  Cloud,
  Container,
  Cpu,
  Database,
  Fan,
  Gamepad2,
  Globe,
  HardDrive,
  HelpCircle,
  Laptop,
  Lightbulb,
  Monitor,
  Network,
  Plug,
  Printer,
  Router,
  Server,
  Shield,
  Smartphone,
  Speaker,
  Tablet,
  Thermometer,
  Tv,
  Watch,
  Wifi,
} from 'lucide-react'
import { useTree } from '../context'

// The curated icon set device types can choose from (Settings → Device types).
export const ICONS = {
  server: Server,
  box: Box,
  container: Container,
  network: Network,
  cpu: Cpu,
  laptop: Laptop,
  smartphone: Smartphone,
  monitor: Monitor,
  tablet: Tablet,
  printer: Printer,
  camera: Camera,
  tv: Tv,
  router: Router,
  wifi: Wifi,
  'hard-drive': HardDrive,
  database: Database,
  shield: Shield,
  gamepad: Gamepad2,
  lightbulb: Lightbulb,
  thermometer: Thermometer,
  speaker: Speaker,
  plug: Plug,
  cloud: Cloud,
  watch: Watch,
  globe: Globe,
  bot: Bot,
  fan: Fan,
  'help-circle': HelpCircle,
}

export function IconGlyph({ icon, size = 15, className = 'text-slate-400' }) {
  const Icon = ICONS[icon] || HelpCircle
  return <Icon size={size} className={className} />
}

export default function DeviceIcon({ type, size = 15 }) {
  const { deviceTypes } = useTree()
  const icon = deviceTypes.find((d) => d.name === type)?.icon
  return (
    <span title={type} aria-label={type}>
      <IconGlyph icon={icon} size={size} />
    </span>
  )
}
