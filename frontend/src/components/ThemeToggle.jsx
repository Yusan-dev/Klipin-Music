import Icon from './Icon.jsx'
import { useTheme } from '../theme.jsx'

/**
 * Sun/moon switch for light/dark mode — Obsidian Vinyl studio toggle.
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
      className={`touch-hit relative inline-flex items-center rounded-full border font-mono text-[10px] uppercase tracking-wider transition-colors ${
        light
          ? 'border-cyan/40 bg-cyan/10 text-cyan-soft hover:bg-cyan/15'
          : 'border-border bg-navy-2/80 text-white/80 hover:border-cyan/40 hover:text-white'
      } ${compact ? 'h-8 px-2.5' : 'h-8 gap-1.5 px-3'}`}
      title="Ganti mode terang/gelap"
    >
      <span
        className={`inline-flex h-4 w-4 items-center justify-center rounded-full transition-transform ${
          light ? 'text-cyan' : 'text-gold'
        }`}
      >
        <Icon name={light ? 'sun' : 'moon'} className="h-3.5 w-3.5" />
      </span>
      {!compact && <span className="hidden sm:inline">{light ? 'Terang' : 'Gelap'}</span>}
    </button>
  )
}