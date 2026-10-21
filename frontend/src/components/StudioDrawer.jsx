import { useState } from 'react'
import { putProjectOptions, rerenderProject, pollJob } from '../api.js'
import { Button, Field } from './ui.jsx'
import Icon from './Icon.jsx'

// Native color picker + hex readout. Zero deps — the OS picker is what users
// expect ("pickup color" feel), and we mirror to a text field for pasting.
function ColorPicker({ label, value, onChange }) {
  const hex = value || '#FFFFFF'
  // normalize to 6-digit hex for the <input type="color"> element
  let norm = hex.replace('#', '')
  if (norm.length === 3) norm = norm.split('').map(c => c + c).join('')
  if (!/^[0-9a-fA-F]{6}$/.test(norm)) norm = 'ffffff'
  return (
    <div className="space-y-1">
      <label className="text-xs font-medium text-white/70">{label}</label>
      <div className="flex items-center gap-2">
        <input
          type="color"
          value={`#${norm}`}
          onChange={(e) => onChange(e.target.value)}
          className="h-9 w-14 cursor-pointer rounded-md border border-border p-0.5"
        />
        <input
          type="text"
          value={hex}
          onChange={(e) => onChange(e.target.value)}
          className="font-mono text-xs"
        />
      </div>
    </div>
  )
}

export default function StudioDrawer({ project, open, onClose, onUpdated }) {
  const opts = project.options || {}
  const [colors, setColors] = useState({
    color: opts.color || '#FFFFFF',
    karaoke_color: opts.karaoke_color || '#FFE500',
    bg: opts.bg || '#000000',
  })
  const [saving, setSaving] = useState(false)
  const [err, setErr] = useState('')
  const [rerendering, setRerendering] = useState(false)

  const saveAndRerender = async () => {
    setErr('')
    setSaving(true)
    try {
      const diff = {
        color: colors.color,
        karaoke_color: colors.karaoke_color,
        bg: colors.bg,
        animation: opts.animation,
        words_per_line: opts.words_per_line,
      }
      await putProjectOptions(project.job_id, diff)
      onUpdated && onUpdated()
      setRerendering(true)
      const { job_id } = await rerenderProject(project.job_id)
      // poll the rerender job until done
      const interval = setInterval(async () => {
        try {
          const status = await pollJob(job_id)
          if (status.status === 'completed' || status.status === 'failed') {
            clearInterval(interval)
            setRerendering(false)
            onUpdated && onUpdated()
          }
        } catch { /* keep polling */ }
      }, 1500)
    } catch (e) {
      setErr(e.message)
    } finally {
      setSaving(false)
    }
  }

  if (!open) return null
  return (
    <div className="fixed inset-0 z-50 flex items-end sm:items-center" role="dialog">
      {/* dimmer */}
      <div className="absolute inset-0 bg-black/40 backdrop-blur-sm" onClick={onClose} />
      {/* drawer */}
      <div className="relative isolate m-0 w-full max-w-lg self-end bg-navy-2/95 shadow-xl border-t border-border sm:m-4 sm:rounded-xl">
        <div className="flex items-center justify-between px-4 py-3">
          <h3 className="text-sm font-medium text-white">Klipin Studio</h3>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 text-white/50 hover:text-white hover:bg-white/10"
            aria-label="Close Studio"
          >
            <Icon name="x" className="h-4 w-4" />
          </button>
        </div>

        <div className="px-4 pb-4 space-y-4 max-h-[70vh] overflow-y-auto">
          <Field label="Warna subtitle (yang belum diucapkan)">
            <ColorPicker value={colors.color} onChange={(v) =>
              setColors(c => ({ ...c, color: v }))} />
          </Field>

          <Field label="Warna karaoke (kata yang sedang diucapkan)">
            <ColorPicker value={colors.karaoke_color} onChange={(v) =>
              setColors(c => ({ ...c, karaoke_color: v }))} />
          </Field>

          <Field label="Warna background teks">
            <ColorPicker value={colors.bg} onChange={(v) =>
              setColors(c => ({ ...c, bg: v }))} />
          </Field>

          {/* passtrhrough existing layout/animation settings */}
          <div className="text-xs text-white/50">
            Gaya lain (animasi, kata/baris) diatur di bawah ini saat membuat klip baru. Ini panel warna + re-render saja.
          </div>
        </div>

        <div className="flex items-center justify-end gap-2 border-t border-border px-4 py-3">
          {err && <span className="text-xs text-danger">{err}</span>}
          <Button variant="ghost" size="sm" onClick={onClose} disabled={saving}>
            Batal
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={saveAndRerender}
            disabled={saving || rerendering}
          >
            {saving ? 'Menyimpan…' : rerendering ? 'Menyusun ulang…' : 'Simpan & Render Ulang'}
          </Button>
        </div>
      </div>
    </div>
  )
}
