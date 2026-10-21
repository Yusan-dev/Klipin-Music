/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // All semantic tokens are CSS custom properties defined in index.css
        // (`:root` = light, `html.dark` = dark). Tailwind emits
        // `rgb(var(--c-*) / <alpha>)` so `bg-navy/60` still works.
        navy: 'rgb(var(--c-navy) / <alpha-value>)',
        'navy-1': 'rgb(var(--c-navy-1) / <alpha-value>)',
        'navy-2': 'rgb(var(--c-navy-2) / <alpha-value>)',
        'navy-3': 'rgb(var(--c-navy-3) / <alpha-value>)',
        panel: 'rgb(var(--c-panel) / <alpha-value>)',
        'panel-2': 'rgb(var(--c-panel-2) / <alpha-value>)',
        border: 'rgb(var(--c-border) / <alpha-value>)',
        'border-soft': 'rgb(var(--c-border-soft) / <alpha-value>)',
        // neon accents (theme-switched: brighter in dark, deeper in light)
        cyan: 'rgb(var(--c-cyan) / <alpha-value>)',
        'cyan-soft': 'rgb(var(--c-cyan-soft) / <alpha-value>)',
        magenta: 'rgb(var(--c-magenta) / <alpha-value>)',
        'magenta-soft': 'rgb(var(--c-magenta-soft) / <alpha-value>)',
        gold: 'rgb(var(--c-gold) / <alpha-value>)',
        'gold-soft': 'rgb(var(--c-gold-soft) / <alpha-value>)',
        // semantic
        signal: 'rgb(var(--c-signal) / <alpha-value>)',
        warn: 'rgb(var(--c-warn) / <alpha-value>)',
        danger: 'rgb(var(--c-danger) / <alpha-value>)',
        // brand accent (used by CTA buttons / highlights)
        brand: 'rgb(var(--c-brand) / <alpha-value>)',
        // `white` follows the ink token so `text-white`, `text-white/70`,
        // `bg-white/10` etc. flip automatically with the theme. Keep real
        // white on always-dark overlays with the `.text-white-solid` class.
        white: 'rgb(var(--c-ink) / <alpha-value>)',
      },
      fontFamily: {
        display: ['Orbitron', '"Chakra Petch"', 'ui-sans-serif', 'sans-serif'],
        sans: ['Rajdhani', '"Segoe UI"', 'system-ui', 'sans-serif'],
        mono: ['"Share Tech Mono"', '"JetBrains Mono"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      boxShadow: {
        'neon-cyan': '0 0 12px rgba(var(--c-cyan), 0.45), 0 0 32px rgba(var(--c-cyan), 0.18)',
        'neon-magenta': '0 0 12px rgba(var(--c-magenta), 0.45), 0 0 32px rgba(var(--c-magenta), 0.18)',
        'neon-gold': '0 0 12px rgba(var(--c-gold), 0.4), 0 0 32px rgba(var(--c-gold), 0.15)',
        'glow-card': '0 0 0 1px rgba(var(--c-cyan), 0.12), 0 8px 32px rgba(var(--c-shadow), 0.55)',
      },
      backgroundImage: {
        'chrome':
          'linear-gradient(180deg, rgb(var(--c-chrome-0)) 0%, rgb(var(--c-chrome-1)) 28%, rgb(var(--c-chrome-2)) 52%, rgb(var(--c-chrome-3)) 76%, rgb(var(--c-chrome-4)) 100%)',
      },
      keyframes: {
        blink: { '0%, 49%': { opacity: '1' }, '50%, 100%': { opacity: '0' } },
        pulseSoft: { '0%, 100%': { opacity: '0.35' }, '50%': { opacity: '1' } },
        sweep: { '0%': { transform: 'translateX(-100%)' }, '100%': { transform: 'translateX(100%)' } },
        gridMove: { '0%': { backgroundPosition: '0 0' }, '100%': { backgroundPosition: '0 48px' } },
        scanline: { '0%': { top: '-10%' }, '100%': { top: '110%' } },
        flicker: {
          '0%, 100%': { opacity: '1' },
          '41%': { opacity: '1' },
          '42%': { opacity: '0.6' },
          '43%': { opacity: '1' },
          '78%': { opacity: '1' },
          '79%': { opacity: '0.7' },
          '80%': { opacity: '1' },
        },
        vu1: { '0%, 100%': { height: '30%' }, '50%': { height: '90%' } },
        vu2: { '0%, 100%': { height: '70%' }, '40%': { height: '20%' }, '70%': { height: '85%' } },
        vu3: { '0%, 100%': { height: '45%' }, '60%': { height: '95%' }, '30%': { height: '25%' } },
        floaty: {
          '0%, 100%': { transform: 'translateY(0)' },
          '50%': { transform: 'translateY(-8px)' },
        },
        marquee: { '0%': { transform: 'translateX(0)' }, '100%': { transform: 'translateX(-50%)' } },
        toastIn: {
          '0%': { transform: 'translateX(110%)', opacity: '0' },
          '100%': { transform: 'translateX(0)', opacity: '1' },
        },
        toastOut: {
          '0%': { transform: 'translateX(0)', opacity: '1' },
          '100%': { transform: 'translateX(110%)', opacity: '0' },
        },
        spinSlow: { '0%': { transform: 'rotate(0deg)' }, '100%': { transform: 'rotate(360deg)' } },
        scissors: {
          '0%, 100%': { transform: 'translateX(0)' },
          '50%': { transform: 'translateX(4px)' },
        },
        shimmer: { '0%': { transform: 'translateX(-120%)' }, '100%': { transform: 'translateX(320%)' } },
        /* karaoke word highlight — used only on always-dark caption chrome */
        karaoke: {
          '0%, 100%': { color: 'rgba(255,255,255,0.55)', textShadow: 'none' },
          '10%, 30%': { color: '#FFD400', textShadow: '0 0 12px rgba(255,212,0,0.75)' },
        },
        /* one-shot success burst */
        celebrate: {
          '0%': { opacity: '0', transform: 'scale(0.4)' },
          '25%': { opacity: '0.8' },
          '100%': { opacity: '0', transform: 'scale(2.4)' },
        },
      },
      animation: {
        blink: 'blink 1.1s step-end infinite',
        'pulse-soft': 'pulseSoft 1.6s ease-in-out infinite',
        sweep: 'sweep 2.2s ease-in-out infinite',
        'grid-move': 'gridMove 1.6s linear infinite',
        scanline: 'scanline 5s linear infinite',
        flicker: 'flicker 4s linear infinite',
        vu1: 'vu1 1.1s ease-in-out infinite',
        vu2: 'vu2 0.9s ease-in-out infinite',
        vu3: 'vu3 1.3s ease-in-out infinite',
        floaty: 'floaty 5s ease-in-out infinite',
        marquee: 'marquee 28s linear infinite',
        'toast-in': 'toastIn 0.22s ease-out',
        'toast-out': 'toastOut 0.2s ease-in forwards',
        'spin-slow': 'spinSlow 2.4s linear infinite',
        scissors: 'scissors 0.9s ease-in-out infinite',
        shimmer: 'shimmer 1.4s ease-in-out infinite',
        karaoke: 'karaoke 2.6s ease-in-out infinite',
        celebrate: 'celebrate 1.2s ease-out forwards',
      },
    },
  },
  plugins: [],
}