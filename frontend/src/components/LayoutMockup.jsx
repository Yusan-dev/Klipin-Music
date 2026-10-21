import { LAYOUTS } from './InputStage.jsx'

// Layout illustrations only; source-based preview lives in the clip editor.
function MiniFrame({ layout, h = 96 }) {
  const w = 54
  const bg = '#1b1d22'
  const blur = 'rgba(120,130,150,0.35)'
  const stroke = '#7C8CF8'

  let inner = null
  switch (layout) {
    case 'general':
    case 'center':
      inner = (
        <rect x={10} y={h * 0.22} width={w - 20} height={h * 0.56}
              fill={blur} rx={4} stroke={stroke} strokeWidth={1.2} />
      )
      break
    case 'face':
    case 'auto':
    case 'track':
      inner = (
        <g>
          <rect x={0} y={0} width={w} height={h} fill={blur} />
          <circle cx={w * 0.5} cy={h * 0.42} r={h * 0.22}
                  fill="#e8c9a0" stroke={stroke} strokeWidth={1.2} />
          <rect x={w * 0.18} y={h * 0.66} width={w * 0.64} height={h * 0.22}
                fill="#3a3d45" rx={3} />
        </g>
      )
      break
    case 'alternate':
      // Active-speaker cuts: one speaker framed at a time, alternating
      // between the two (distinct from SPLIT stack and single FACE lock).
      inner = (
        <g>
          <rect x={0} y={0} width={w} height={h} fill={blur} />
          <circle cx={w * 0.32} cy={h * 0.42} r={h * 0.16}
                  fill="#e8c9a0" stroke={stroke} strokeWidth={1} />
          <circle cx={w * 0.68} cy={h * 0.42} r={h * 0.16}
                  fill="#c9b0e8" stroke={stroke} strokeWidth={1}
                  strokeDasharray="2 1.5" opacity={0.55} />
          <text x={w * 0.5} y={h * 0.78} textAnchor="middle"
                fontSize={h * 0.13} fill={stroke}>⇄</text>
        </g>
      )
      break
    case 'split':
      inner = (
        <g>
          <rect x={0} y={0} width={w} height={h / 2} fill={blur} />
          <rect x={0} y={h / 2} width={w} height={h / 2} fill={blur} />
          <circle cx={w * 0.5} cy={h * 0.27} r={h * 0.13}
                  fill="#e8c9a0" stroke={stroke} strokeWidth={1} />
          <circle cx={w * 0.5} cy={h * 0.75} r={h * 0.13}
                  fill="#c9b0e8" stroke={stroke} strokeWidth={1} />
        </g>
      )
      break
    case 'panel':
      inner = (
        <g>
          <rect x={0} y={0} width={w / 2 - 1} height={h} fill={blur} />
          <rect x={w / 2 + 1} y={0} width={w / 2 - 1} height={h} fill={blur} />
          {[[0.25,0.42],[0.75,0.42]].map(([cx,cy],i)=>(
            <circle key={i} cx={w*cx} cy={h*cy} r={h*0.1}
                    fill={['#e8c9a0','#c9b0e8','#a0e8c9','#e8a0b0'][i]}
                    stroke={stroke} strokeWidth={0.8} />
          ))}
        </g>
      )
      break
    case 'gameplay':
    case 'screencast':
      inner = (
        <rect x={6} y={h * 0.12} width={w - 12} height={h * 0.76}
              fill="#2a2d35" rx={3} stroke={stroke} strokeWidth={1.2} />
      )
      break
    case 'square':
      inner = (
        <rect x={w*0.12} y={h*0.12} width={w*0.76} height={h*0.76}
              fill={blur} rx={4} stroke={stroke} strokeWidth={1.2} />
      )
      break
    default:
      inner = <rect x={8} y={h*0.2} width={w-16} height={h*0.6} fill={blur} rx={4} />
  }
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className="h-24 w-[54px] rounded-md border border-border-soft bg-navy-1"
         preserveAspectRatio="xMidYMid meet">
      <rect x={0} y={0} width={w} height={h} fill={bg} />
      {inner}
    </svg>
  )
}

export default function LayoutMockup({ value, onChange }) {
  return (
    <div className="grid grid-cols-5 gap-2 sm:grid-cols-5">
      {LAYOUTS.map((l) => {
        const active = (value || 'auto') === l.id
        return (
          <button
            key={l.id}
            type="button"
            onClick={() => onChange(l.id)}
            title={l.desc}
            className={`flex flex-col items-center gap-1 rounded-md border p-1.5 transition ${
              active ? 'border-cyan bg-cyan/15' : 'border-border bg-navy-2 hover:border-white/30'
            }`}
          >
            <MiniFrame layout={l.id} />
            <span className={`font-mono text-[8px] uppercase tracking-wider ${active ? 'text-cyan-soft' : 'text-white/50'}`}>
              {l.id}
            </span>
          </button>
        )
      })}
    </div>
  )
}
