import { Micro } from './ui.jsx'

/**
 * Klipin loading visual — an animated "scissors clipping a film frame"
 * mark inside a rotating neon ring, with a shimmer progress line.
 * Honors prefers-reduced-motion via the existing global CSS (animations
 * are Tailwind utilities gated by the motion-safe variant in index.css).
 */
export default function Loader({ size = 120, label = 'memuat…', tone = 'cyan' }) {
  const ring = tone === 'magenta'
    ? 'from-magenta via-cyan to-magenta'
    : 'from-cyan via-magenta to-cyan'
  return (
    <div className="flex flex-col items-center gap-4 py-10" role="status" aria-label={label}>
      <div className="relative" style={{ width: size, height: size }}>
        {/* rotating gradient ring */}
        <span
          aria-hidden
          className={`absolute inset-0 rounded-full bg-gradient-to-tr ${ring} opacity-60 blur-[6px] animate-spin-slow`}
        />
        {/* soft pulse halo */}
        <span aria-hidden className="absolute inset-0 animate-ping rounded-full bg-cyan/10" />
        {/* inner disc */}
        <span aria-hidden className="absolute inset-[6px] flex items-center justify-center rounded-full bg-navy shadow-neon-cyan" />
        {/* scissors + film frame mark */}
        <span aria-hidden className="absolute inset-0 flex items-center justify-center">
          <svg viewBox="0 0 96 96" className="h-3/5 w-3/5" fill="none">
            <defs>
              <linearGradient id="klipin-loader" x1="0" y1="0" x2="96" y2="96">
                <stop stopColor="#00F0FF" />
                <stop offset="0.55" stopColor="#4D8DFF" />
                <stop offset="1" stopColor="#FF2EAE" />
              </linearGradient>
            </defs>
            <rect x="26" y="18" width="44" height="34" rx="5" stroke="url(#klipin-loader)" strokeWidth="3" />
            <rect x="34" y="28" width="10" height="14" rx="2" stroke="#00F0FF" strokeWidth="2" opacity="0.8" />
            <rect x="52" y="28" width="10" height="14" rx="2" stroke="#00F0FF" strokeWidth="2" opacity="0.8" />
            {/* scissors */}
            <g className="animate-scissors">
              <path d="M20 64 L34 50 M20 78 L34 64" stroke="#FFD400" strokeWidth="3" strokeLinecap="round" />
              <circle cx="20" cy="64" r="4" fill="#FFD400" />
              <circle cx="20" cy="78" r="4" fill="#FFD400" />
              <path d="M34 50 L56 40 M34 64 L56 54" stroke="#FF2EAE" strokeWidth="3" strokeLinecap="round" />
            </g>
            {/* falling sparkles */}
            <g>
              <circle cx="62" cy="70" r="2" fill="#00F0FF" className="animate-bounce" />
              <circle cx="72" cy="80" r="1.6" fill="#FF2EAE" className="animate-bounce" style={{ animationDelay: '0.15s' }} />
              <circle cx="80" cy="64" r="1.4" fill="#FFD400" className="animate-bounce" style={{ animationDelay: '0.3s' }} />
            </g>
          </svg>
        </span>
      </div>
      <div className="flex w-44 flex-col items-center gap-2">
        <Micro className="text-center">{label}</Micro>
        {/* shimmer progress line */}
        <span aria-hidden className="relative h-1 w-full overflow-hidden rounded-full bg-white/10">
          <span className="absolute inset-y-0 left-0 w-1/3 animate-shimmer rounded-full bg-gradient-to-r from-transparent via-cyan to-transparent" />
        </span>
      </div>
    </div>
  )
}
