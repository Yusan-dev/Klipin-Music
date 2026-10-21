import Icon from './Icon.jsx'
import { useTheme } from '../theme.jsx'

/**
 * Sun/moon switch for light/dark mode. A retro pill with a sliding knob —
 * matches the machine styling of the rest of the app.
 */
export default function ThemeToggle({ compact = false }) {
  const { theme, toggle } = useTheme()
  const light = theme === 'light'

  return (
    <button
      type="button"
      role="switch"
      aria-checked={light}
      aria-label={light ? 'Mode terang — klik untuk mode gelap' : 'Mode gelap — klik untuk mode terang'}
      onClick={toggle}
      className={`relative inline-flex items-center rounded-full border font-mono text-[10px] uppercase tracking-widest transition-colors ${
        light
          ? 'border-cyan/50 bg-cyan/15 text-cyan-soft'
          : 'border-border bg-navy-2/70 text-cyan-soft/80'
      } ${compact ? 'h-6 px-2' : 'h-7 gap-1.5 px-2.5'}`}
      title="Ganti mode terang/gelap"
    >
      <span
        className={`inline-flex h-4 w-4 items-center justify-center rounded-full transition-transform ${
          light ? 'translate-x-0 text-gold' : 'text-magenta-soft'
        }`}
      >
        <Icon name={light ? 'sun' : 'moon'} className="h-3.5 w-3.5" />
      </span>
      {!compact && <span className="hidden sm:inline">{light ? 'Terang' : 'Gelap'}</span>}
    </button>
  )
}