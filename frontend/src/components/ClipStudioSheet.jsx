import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import Icon from './Icon.jsx'
import Waveform from './Waveform.jsx'
import { Button, Micro, Spinner } from './ui.jsx'
import { createClipProxy, getCalibration, getClipLayerPlan, getWaveform, pollJob, putClipLayerPlan, putClipLayoutPlan, renderClipLayerPlan, resolveAsset, setClipApproval, uploadClipLayerMedia } from '../api.js'

const TOOLS = [
  { id: 'trim', icon: 'trim', label: 'Trim', hint: 'Cut dan rentang scene' },
  { id: 'captions', icon: 'comments', label: 'Captions', hint: 'Teks, timing, dan gaya' },
  { id: 'layout', icon: 'mobile', label: 'Layout', hint: 'Reframe dan subject lock' },
  { id: 'broll', icon: 'broll', label: 'B-roll', hint: 'Visual pendukung' },
  { id: 'audio', icon: 'sound', label: 'Audio', hint: 'Clean, SFX, dub, voiceover' },
  { id: 'effects', icon: 'fx', label: 'Effects', hint: 'FX dan enhance' },
  { id: 'insert', icon: 'stack', label: 'Insert', hint: 'Teks, gambar, audio' },
  { id: 'brand', icon: 'paint', label: 'Brand', hint: 'Gaya caption dan brand' },
  { id: 'publish', icon: 'publish', label: 'Publish', hint: 'YouTube, TikTok, Instagram' },
]
const INSIGHTS = [
  { id: 'insights', icon: 'analytics', label: 'Insights', hint: 'Analytics dan calibration' },
  { id: 'repurpose', icon: 'repurpose', label: 'Repurpose', hint: 'Konten lintas platform' },
  { id: 'thumbnail', icon: 'thumbnail', label: 'Thumbnail', hint: 'Frame dan thumbnail AI' },
]

export default function ClipStudioSheet(props) {
  const { open, onClose, clip, jobId } = props
  const [activeTool, setActiveTool] = useState('trim')
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [timelineSelection, setTimelineSelection] = useState(null)
  const [draftScenes, setDraftScenes] = useState([])
  const [undoStack, setUndoStack] = useState([])
  const [redoStack, setRedoStack] = useState([])
  const [planState, setPlanState] = useState('idle')
  const [planMessage, setPlanMessage] = useState('')
  const [draftLayers, setDraftLayers] = useState([])
  const [layerUndoStack, setLayerUndoStack] = useState([])
  const [layerRedoStack, setLayerRedoStack] = useState([])
  const [selectedLayerId, setSelectedLayerId] = useState('')
  const [layerDirty, setLayerDirty] = useState(false)
  const [layerState, setLayerState] = useState('idle')
  const [layerMessage, setLayerMessage] = useState('')
  const [wave, setWave] = useState(null)
  const [autoSave, setAutoSave] = useState(true)
  const [previewQuality, setPreviewQuality] = useState('eco')
  const [proxyUrl, setProxyUrl] = useState('')
  const [calib, setCalib] = useState(null)
  const [calibBusy, setCalibBusy] = useState(false)
  const [calibErr, setCalibErr] = useState('')
  const videoRef = useRef(null)
  const dialogRef = useRef(null)
  const calibRequested = useRef(false)
  const dirtyRef = useRef(false)
  const historyActions = useRef({undo:()=>{},redo:()=>{}})
  const editorActions = useRef({remove:()=>{},duplicate:()=>{}})
  dirtyRef.current = undoStack.length > 0 || layerDirty
  const duration = Math.max(0, Number(clip?.end || 0) - Number(clip?.start || 0))
  const progress = duration ? Math.min(100, currentTime / duration * 100) : 0
  const active = useMemo(() => [...TOOLS, ...INSIGHTS].find((t) => t.id === activeTool) || TOOLS[0], [activeTool])

  useEffect(() => {
    if (!open) return
    setActiveTool('trim'); setCurrentTime(0); setPlaying(false); setTimelineSelection(null)
    setDraftScenes(initialScenes(clip, duration)); setUndoStack([]); setRedoStack([])
    setDraftLayers(normalizeLayers(clip?.layer_plan, duration)); setLayerUndoStack([]); setLayerRedoStack([]); setSelectedLayerId(''); setLayerDirty(false)
    setPlanState('idle'); setPlanMessage(''); calibRequested.current = false
    const previousFocus=document.activeElement
    const focusTimer=window.setTimeout(()=>dialogRef.current?.querySelector('[data-studio-focus]')?.focus(),0)
    const onKey = (e) => {
      if (e.key === 'Escape') closeWithGuard(onClose, dirtyRef)
      if (e.key === 'Tab'&&dialogRef.current) {
        const focusable=[...dialogRef.current.querySelectorAll('button:not([disabled]),select:not([disabled]),input:not([disabled]),textarea:not([disabled]),a[href]')]
        if(focusable.length){const first=focusable[0];const last=focusable[focusable.length-1];if(e.shiftKey&&document.activeElement===first){e.preventDefault();last.focus()}else if(!e.shiftKey&&document.activeElement===last){e.preventDefault();first.focus()}}
      }
      if (['INPUT','SELECT','TEXTAREA'].includes(e.target?.tagName)) return
      if (e.key === ' ') { e.preventDefault(); toggleVideo(videoRef) }
      if (e.key === 'ArrowLeft') { e.preventDefault(); seekVideo(videoRef, Math.max(0, (videoRef.current?.currentTime || 0) - 1), setCurrentTime) }
      if (e.key === 'ArrowRight') { e.preventDefault(); seekVideo(videoRef, Math.min(duration, (videoRef.current?.currentTime || 0) + 1), setCurrentTime) }
      if ((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='z') { e.preventDefault(); e.shiftKey?historyActions.current.redo():historyActions.current.undo() }
      if ((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='y') { e.preventDefault(); historyActions.current.redo() }
      if ((e.key==='Delete'||e.key==='Backspace')) { e.preventDefault(); editorActions.current.remove() }
      if ((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='d') { e.preventDefault(); editorActions.current.duplicate() }
    }
    window.addEventListener('keydown', onKey)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { window.clearTimeout(focusTimer); window.removeEventListener('keydown', onKey); document.body.style.overflow = previous; previousFocus?.focus?.() }
  }, [open, duration])

  useEffect(() => {
    if (!open || !jobId || clip?.index == null) return
    let activeRequest = true
    getClipLayerPlan(jobId, clip.index).then((data) => {
      if (!activeRequest) return
      setDraftLayers(normalizeLayers(data.layers, duration)); setLayerUndoStack([]); setLayerRedoStack([]); setLayerDirty(false)
    }).catch((error) => activeRequest && setLayerMessage(error.message))
    return () => { activeRequest = false }
  }, [open, jobId, clip?.index, duration])

  useEffect(() => {
    if (!open || !jobId) return
    let alive=true; getWaveform(jobId).then((data)=>alive&&setWave(data)).catch(()=>alive&&setWave(null))
    return ()=>{alive=false}
  }, [open,jobId])

  useEffect(()=>{
    if(!open||!jobId||clip?.index==null)return
    let alive=true
    createClipProxy(jobId,clip.index).then(async(data)=>{ if(data.ready)return data; const done=await pollJob(data.job_id,{interval:1200}); return done.result }).then((result)=>alive&&setProxyUrl(result?.proxy_url||'')).catch(()=>alive&&setProxyUrl(''))
    return()=>{alive=false}
  },[open,jobId,clip?.index,clip?.rev])

  useEffect(() => {
    if (!open || !autoSave || !layerDirty || !jobId || clip?.index == null) return
    const timer=window.setTimeout(async()=>{
      try { const result=await putClipLayerPlan(jobId,clip.index,draftLayers); rememberVersion(jobId,clip.index,result.layers); setLayerDirty(false); setLayerMessage('Autosaved') }
      catch(error){ setLayerMessage(`Autosave gagal · ${error.message}`) }
    },1400)
    return ()=>window.clearTimeout(timer)
  },[open,autoSave,layerDirty,draftLayers,jobId,clip?.index])

  useEffect(() => {
    if (activeTool !== 'insights' || calib !== null || calibBusy || calibRequested.current || !jobId) return
    calibRequested.current = true
    setCalibBusy(true); setCalibErr('')
    getCalibration(jobId).then(setCalib).catch((e) => setCalibErr(e.message)).finally(() => setCalibBusy(false))
  }, [activeTool, calib, calibBusy, jobId])

  if (!open) return null
  const requestClose = () => closeWithGuard(onClose, dirtyRef)
  const run = (fn) => { if (!closeWithGuard(onClose, dirtyRef)) return; setTimeout(() => fn?.(), 0) }
  const select = (id) => {
    setActiveTool(id)
    if (id === 'captions' && !props.capOpen) props.onCapToggle?.()
    if (id === 'insights' && !props.statsOpen) props.onToggleStats?.()
  }
  const actions = {
    trim: props.onTrim, captions: props.onEdit, layout: props.onTrim,
    broll: props.onBroll, effects: props.onFx, insert: props.onInsert,
    brand: props.onEdit, publish: props.onPublish, repurpose: props.onRepurpose,
    thumbnail: props.onThumbnail,
  }
  const seek = (seconds) => seekVideo(videoRef, Math.max(0, Math.min(duration, seconds)), setCurrentTime)
  const changeLayers = (next, message = 'Perubahan layer belum disimpan') => {
    setLayerUndoStack((stack)=>[...stack.slice(-29), cloneLayers(draftLayers)])
    setLayerRedoStack([])
    setDraftLayers(next)
    setLayerDirty(true)
    setLayerMessage(message)
  }
  const addLayer = (kind = 'text') => {
    const id = `layer-${Date.now().toString(36)}`
    const layer = {id,kind,label:kind==='channel'?'Nama channel':kind==='broll'?'B-roll':'Text',text:kind==='channel'?'@namachannel':kind==='text'?'Tulis teks':'',start:Number(currentTime.toFixed(2)),end:Math.min(duration,Number((currentTime+Math.min(4,duration)).toFixed(2))),x:.5,y:kind==='channel'?.1:.25,width:.72,opacity:1,visible:true,locked:false,color:'#FFFFFF'}
    changeLayers([...draftLayers,layer], 'Layer baru · belum disimpan'); setSelectedLayerId(id)
  }
  const updateLayer = (id, patch) => {
    const candidates=[0,duration,currentTime,...draftScenes.flatMap((scene)=>[scene.start,scene.end]),...(props.captionLines||[]).flatMap((line)=>[(Number(line.start)||0)-(Number(clip.start)||0),(Number(line.end)||0)-(Number(clip.start)||0)])]
    const nextPatch={...patch}
    for(const key of ['start','end']) if(Number.isFinite(nextPatch[key])) { const near=candidates.find((point)=>Math.abs(point-nextPatch[key])<=.15); if(near!==undefined)nextPatch[key]=Math.max(0,Math.min(duration,near)) }
    if(Number.isFinite(nextPatch.x))nextPatch.x=snapUnit(nextPatch.x); if(Number.isFinite(nextPatch.y))nextPatch.y=snapUnit(nextPatch.y)
    changeLayers(draftLayers.map((layer)=>layer.id===id?{...layer,...nextPatch}:layer))
  }
  const deleteLayer = (id) => { changeLayers(draftLayers.filter((layer)=>layer.id!==id), 'Layer dihapus · belum disimpan'); setSelectedLayerId('') }
  const duplicateLayer = (id) => { const source=draftLayers.find((layer)=>layer.id===id); if(!source)return; const copy={...source,id:`layer-${Date.now().toString(36)}`,label:`${source.label} copy`,x:Math.min(1,source.x+.03),y:Math.min(1,source.y+.03)}; changeLayers([...draftLayers,copy],'Layer diduplikasi'); setSelectedLayerId(copy.id) }
  const uploadLayer = async (file,kind) => { setLayerState('saving'); setLayerMessage('Mengunggah media…'); try { const result=await uploadClipLayerMedia(jobId,clip.index,file,{kind,start:currentTime,end:Math.min(duration,currentTime+4)}); setDraftLayers(normalizeLayers(result.layers,duration)); setSelectedLayerId(result.layer.id); setLayerDirty(false); setLayerState('idle'); setLayerMessage('Media menjadi layer editable') } catch(error){setLayerState('error');setLayerMessage(error.message)} }
  const restoreVersion = () => { try { const versions=JSON.parse(localStorage.getItem(`klipin-layer-versions:${jobId}:${clip.index}`)||'[]'); const version=versions.at(-2)||versions.at(-1); if(!version)return setLayerMessage('Belum ada versi sebelumnya'); changeLayers(normalizeLayers(version.layers,duration),'Versi sebelumnya dipulihkan · autosave segera berjalan') } catch { setLayerMessage('Version history tidak tersedia') } }
  const saveLayers = async () => {
    setLayerState('saving'); setLayerMessage('Menyimpan layer…')
    try { const result=await putClipLayerPlan(jobId,clip.index,draftLayers); rememberVersion(jobId,clip.index,result.layers); setDraftLayers(normalizeLayers(result.layers,duration)); setLayerUndoStack([]); setLayerRedoStack([]); setLayerDirty(false); setLayerState('idle'); setLayerMessage('Layer plan tersimpan'); props.onClipUpdate?.({index:clip.index,layer_plan:result.layers}) }
    catch(error){ setLayerState('error'); setLayerMessage(error.message) }
  }
  const renderLayers = async () => {
    if (layerDirty) { setLayerMessage('Simpan layer plan sebelum render'); return }
    setLayerState('saving'); setLayerMessage('Merender layer di backend…')
    try { const queued=await renderClipLayerPlan(jobId,clip.index); const data=await pollJob(queued.job_id,{interval:1200}); setLayerState('idle'); setLayerMessage('Render layer selesai'); props.onClipUpdate?.({...data.result,index:clip.index}) }
    catch(error){ setLayerState('error'); setLayerMessage(error.message) }
  }
  const changeDraft = (next) => {
    setUndoStack((stack) => [...stack.slice(-29), cloneScenes(draftScenes)])
    setRedoStack([])
    setDraftScenes(next)
    setPlanMessage('Perubahan belum disimpan')
  }
  const undo = () => {
    if (layerUndoStack.length) {
      const previous=layerUndoStack[layerUndoStack.length-1]
      setLayerRedoStack((stack)=>[...stack.slice(-29),cloneLayers(draftLayers)])
      setLayerUndoStack((stack)=>stack.slice(0,-1)); setDraftLayers(previous); setLayerDirty(true)
      setLayerMessage('Undo layer · perubahan belum disimpan'); return
    }
    if (!undoStack.length) return
    const previous = undoStack[undoStack.length - 1]
    setRedoStack((stack) => [...stack, cloneScenes(draftScenes)])
    setUndoStack((stack) => stack.slice(0, -1))
    setDraftScenes(previous)
    setTimelineSelection(null)
    setPlanMessage('Undo · perubahan belum disimpan')
  }
  const redo = () => {
    if (layerRedoStack.length) {
      const next=layerRedoStack[layerRedoStack.length-1]
      setLayerUndoStack((stack)=>[...stack.slice(-29),cloneLayers(draftLayers)])
      setLayerRedoStack((stack)=>stack.slice(0,-1)); setDraftLayers(next); setLayerDirty(true)
      setLayerMessage('Redo layer · perubahan belum disimpan'); return
    }
    if (!redoStack.length) return
    const next = redoStack[redoStack.length - 1]
    setUndoStack((stack) => [...stack, cloneScenes(draftScenes)])
    setRedoStack((stack) => stack.slice(0, -1))
    setDraftScenes(next)
    setTimelineSelection(null)
    setPlanMessage('Redo · perubahan belum disimpan')
  }
  const splitScene = () => {
    const index = draftScenes.findIndex((scene) => currentTime > scene.start + .5 && currentTime < scene.end - .5)
    if (index < 0) { setPlanMessage('Playhead harus berada minimal 0,5 detik dari batas scene'); return }
    const scene = draftScenes[index]
    const point = Number(currentTime.toFixed(2))
    changeDraft([...draftScenes.slice(0,index), {...scene,end:point}, {...scene,start:point}, ...draftScenes.slice(index+1)])
    setTimelineSelection({type:'scene',index:index+1,label:scene.layout,start:point,end:scene.end})
  }
  const deleteScene = (index) => {
    if (draftScenes.length <= 1 || index < 0) return
    const next=cloneScenes(draftScenes)
    const removed=next[index]
    next.splice(index,1)
    if(index===0) next[0].start=removed.start
    else next[index-1].end=removed.end
    changeDraft(next)
    setTimelineSelection(null)
  }
  const setSceneLayout = (index, layout) => {
    changeDraft(draftScenes.map((scene,i)=>i===index?{...scene,layout,strategy:layout}:scene))
    setTimelineSelection((selected)=>selected?.type==='scene'&&selected.index===index?{...selected,label:layout}:selected)
  }
  const savePlan = async () => {
    if (!undoStack.length) return
    setPlanState('saving'); setPlanMessage('Menyimpan layout…')
    try {
      const result = await putClipLayoutPlan(jobId, clip.index, draftScenes.map(({start,end,layout})=>({start,end,layout})))
      const saved = (result.layout_plan || []).map(normalizeScene)
      setDraftScenes(saved.length ? saved : initialScenes(clip,duration))
      setUndoStack([]); setRedoStack([]); setPlanState('idle'); setPlanMessage('Layout tersimpan · re-render untuk menerapkan')
      props.onClipUpdate?.({index:clip.index,layout_plan:saved,layout_plan_edited:result.layout_plan_edited})
    } catch (error) {
      setPlanState('error'); setPlanMessage(error.message)
    }
  }
  historyActions.current={undo,redo}
  editorActions.current={remove:()=>selectedLayerId&&deleteLayer(selectedLayerId),duplicate:()=>selectedLayerId&&duplicateLayer(selectedLayerId)}

  return createPortal((
    <div className="fixed inset-0 z-[80]" role="dialog" aria-modal="true" aria-label="Klip-in Studio editor">
      <div className="absolute inset-0 bg-black/80 backdrop-blur-sm" onClick={requestClose} />
      <section ref={dialogRef} className="card-chrome absolute inset-0 flex flex-col overflow-hidden border-cyan/20 bg-navy-1 shadow-neon-cyan sm:inset-3 sm:rounded-2xl sm:border lg:inset-5">
        <Header clip={clip} onClose={requestClose} onPreview={() => toggleVideo(videoRef)} onExport={() => run(props.onPublish)} onUndo={undo} onRedo={redo} canUndo={undoStack.length>0||layerUndoStack.length>0} canRedo={redoStack.length>0||layerRedoStack.length>0} />
        <div className="min-h-0 flex-1 overflow-y-auto lg:grid lg:grid-cols-[72px_minmax(320px,1fr)_320px] lg:overflow-hidden">
          <ToolRail active={activeTool} onSelect={select} />
          <Canvas clip={clip} videoRef={videoRef} playing={playing} setPlaying={setPlaying} currentTime={currentTime} setCurrentTime={setCurrentTime} duration={duration} layers={draftLayers} selectedLayerId={selectedLayerId} onSelectLayer={setSelectedLayerId} onMoveLayer={updateLayer} previewQuality={previewQuality} proxyUrl={proxyUrl}/>
          <Inspector active={active} activeTool={activeTool} props={props} calib={calib} calibBusy={calibBusy} calibErr={calibErr} run={run} action={() => run(actions[activeTool])} layers={draftLayers} selectedLayerId={selectedLayerId} onSelectLayer={setSelectedLayerId} onAddLayer={addLayer} onUpdateLayer={updateLayer} onDeleteLayer={deleteLayer} onDuplicateLayer={duplicateLayer} onUploadLayer={uploadLayer} onRestoreVersion={restoreVersion} onSaveLayers={saveLayers} onRenderLayers={renderLayers} layerDirty={layerDirty} layerState={layerState} layerMessage={layerMessage} autoSave={autoSave} onAutoSave={setAutoSave} previewQuality={previewQuality} onPreviewQuality={setPreviewQuality}/>
        </div>
        <Timeline clip={clip} scenes={draftScenes} captions={props.captionLines || []} layers={draftLayers} selectedLayerId={selectedLayerId} onSelectLayer={setSelectedLayerId} progress={progress} current={currentTime} duration={duration} selected={timelineSelection} onSelect={setTimelineSelection} onSeek={seek} onOpenEditor={() => run(props.onTrim)} onSplit={splitScene} onDelete={deleteScene} onLayout={setSceneLayout} onSave={savePlan} dirty={undoStack.length>0} planState={planState} planMessage={planMessage} wave={wave} />
        <MobileBar active={activeTool} onSelect={select} />
      </section>
    </div>
  ), document.body)
}

function Header({ clip, onClose, onPreview, onExport, onUndo, onRedo, canUndo, canRedo }) {
  return <header className="flex h-16 shrink-0 items-center gap-3 border-b border-border-soft bg-navy-2/85 px-3 sm:px-5">
    <div className="min-w-0 flex-1"><Micro className="block text-[9px]">KLIP-IN STUDIO</Micro><div className="flex items-center gap-2"><h2 className="truncate text-sm font-semibold text-white">{clip.title || `Klip ${clip.index + 1}`}</h2><span className="hidden font-mono text-[9px] text-white/35 sm:inline">{fmt(clip.start)} – {fmt(clip.end)}</span></div></div>
    <div className="hidden items-center gap-1 sm:flex"><HeaderIcon icon="undo" label="Undo layout" disabled={!canUndo} onClick={onUndo}/><HeaderIcon icon="sync" label="Redo layout" disabled={!canRedo} onClick={onRedo}/><span className="mx-1 h-5 w-px bg-border-soft"/><Button variant="ghost" size="sm" onClick={onPreview}><Icon name="play" className="h-3.5 w-3.5"/> Preview</Button><Button variant="cyan" size="sm" onClick={onExport}><Icon name="publish" className="h-3.5 w-3.5"/> Export</Button></div>
    <button type="button" data-studio-focus onClick={onClose} aria-label="Tutup studio" className="rounded-md border border-border p-2 text-white/55 hover:text-white"><Icon name="close" className="h-4 w-4"/></button>
  </header>
}
function HeaderIcon({ icon, label, disabled, onClick }) { return <button type="button" title={label} aria-label={label} disabled={disabled} onClick={onClick} className="rounded p-2 text-white/35 hover:bg-white/5 disabled:opacity-25"><Icon name={icon} className="h-4 w-4"/></button> }

function ToolRail({ active, onSelect }) {
  return <nav aria-label="Editor tools" className="hidden overflow-y-auto border-r border-border-soft bg-navy-2/55 py-3 lg:block"><div className="flex flex-col items-center gap-1">{TOOLS.map((t) => <RailButton key={t.id} tool={t} active={active === t.id} onClick={() => onSelect(t.id)}/>)}<div className="my-2 h-px w-8 bg-border-soft"/>{INSIGHTS.map((t) => <RailButton key={t.id} tool={t} active={active === t.id} onClick={() => onSelect(t.id)}/>)}</div></nav>
}
function RailButton({ tool, active, onClick }) { return <button type="button" onClick={onClick} title={tool.label} aria-label={tool.label} className={`group relative flex h-11 w-12 items-center justify-center rounded-lg border transition ${active ? 'border-cyan/60 bg-cyan/15 text-cyan-soft shadow-neon-cyan' : 'border-transparent text-white/40 hover:bg-white/5 hover:text-white'}`}><Icon name={tool.icon} className="h-5 w-5"/><span className="pointer-events-none absolute left-full z-30 ml-2 hidden whitespace-nowrap rounded border border-border bg-navy-1 px-2 py-1 text-[11px] text-white group-hover:block group-focus:block">{tool.label}</span></button> }

function Canvas({ clip, videoRef, playing, setPlaying, currentTime, setCurrentTime, duration, layers, selectedLayerId, onSelectLayer, onMoveLayer, previewQuality, proxyUrl }) {
  const toggle = () => toggleVideo(videoRef)
  const dragLayer = (event, layer) => {
    if (layer.locked) return
    event.preventDefault(); event.stopPropagation(); onSelectLayer(layer.id)
    const frame=event.currentTarget.parentElement.getBoundingClientRect()
    const move=(e)=>onMoveLayer(layer.id,{x:snapUnit((e.clientX-frame.left)/frame.width),y:snapUnit((e.clientY-frame.top)/frame.height)})
    const stop=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',stop)}
    window.addEventListener('pointermove',move);window.addEventListener('pointerup',stop)
  }
  const resizeLayer=(event,layer)=>{if(layer.locked)return;event.preventDefault();event.stopPropagation();const frame=event.currentTarget.parentElement.parentElement.getBoundingClientRect();const move=(e)=>onMoveLayer(layer.id,{width:Math.max(.05,Math.min(1,Math.abs(e.clientX-(frame.left+frame.width*layer.x))*2/frame.width))});const stop=()=>{window.removeEventListener('pointermove',move);window.removeEventListener('pointerup',stop)};window.addEventListener('pointermove',move);window.addEventListener('pointerup',stop)}
  const activeLayers=(layers||[]).filter((layer)=>layer.visible!==false&&currentTime>=layer.start&&currentTime<=layer.end)
  const warnings=activeLayers.filter(layer=>layer.kind!=='audio'&&((layer.y>.68)||(layer.y<.16)||(Math.abs(layer.x-.5)<.18&&layer.width>.48)))
  return <main className="flex min-h-[34rem] flex-col bg-black/20 px-3 py-4 sm:px-5 lg:min-h-0 lg:overflow-y-auto">
    <div className="mx-auto flex w-full max-w-3xl flex-1 items-center justify-center"><div className="relative aspect-[9/16] max-h-[62vh] w-auto max-w-full overflow-hidden rounded-xl border border-white/15 bg-black shadow-2xl lg:max-h-[calc(100vh-240px)]">
      <video ref={videoRef} src={resolveAsset(previewQuality==='eco'&&proxyUrl?proxyUrl:clip.video_url)} poster={clip.thumbnail_url ? resolveAsset(clip.thumbnail_url) : undefined} playsInline preload="metadata" onPlay={() => setPlaying(true)} onPause={() => setPlaying(false)} onEnded={() => setPlaying(false)} onTimeUpdate={(e) => setCurrentTime(e.currentTarget.currentTime || 0)} className="h-full w-full object-contain"/>
      {activeLayers.filter(layer=>layer.kind!=='audio').map((layer)=><button key={layer.id} type="button" aria-label={`layer ${layer.label}`} onPointerDown={(event)=>dragLayer(event,layer)} className={`absolute z-10 -translate-x-1/2 -translate-y-1/2 touch-none overflow-visible rounded border text-center shadow-lg ${selectedLayerId===layer.id?'border-cyan ring-2 ring-cyan/30':'border-white/30'}`} style={{left:`${layer.x*100}%`,top:`${layer.y*100}%`,width:`${layer.width*100}%`,opacity:layer.opacity,color:layer.color||'#fff',background:(layer.kind==='broll'||layer.kind==='image')?'transparent':'rgba(0,0,0,.48)',fontSize:layer.kind==='channel'?'11px':'14px',transform:`translate(-50%,-50%) rotate(${layer.rotation||0}deg)`}}>{layer.kind==='image'&&layer.asset_url?<img src={resolveAsset(layer.asset_url)} alt="" className="pointer-events-none block w-full rounded object-contain"/>:layer.kind==='broll'&&layer.asset_url?<video src={resolveAsset(layer.asset_url)} muted loop autoPlay playsInline preload={previewQuality==='eco'?'metadata':'auto'} className="pointer-events-none block w-full rounded object-cover"/>:layer.kind==='broll'?<span className="block bg-cyan/20 px-2 py-3"><Icon name="broll" className="mr-1 h-3 w-3"/>B-ROLL</span>:<span className="block px-2 py-1">{layer.text||layer.label}</span>}{selectedLayerId===layer.id&&!layer.locked&&<span onPointerDown={(event)=>resizeLayer(event,layer)} aria-hidden className="absolute -bottom-2 -right-2 h-4 w-4 cursor-nwse-resize rounded-full border-2 border-black bg-cyan"/>}</button>)}
      <SafeZones/><button type="button" onClick={toggle} aria-label={playing ? 'Pause preview' : 'Play preview'} className="absolute inset-0 m-auto flex h-14 w-14 items-center justify-center rounded-full border border-white/25 bg-black/55 text-white backdrop-blur hover:scale-105"><Icon name={playing ? 'pause' : 'play'} className="h-6 w-6"/></button><span className="absolute left-2 top-2 rounded bg-black/65 px-2 py-1 font-mono text-[9px] text-white/75">9:16 · SAFE PREVIEW</span>
      {!!warnings.length&&<span className="absolute bottom-2 left-2 right-2 z-30 rounded border border-gold/50 bg-black/80 px-2 py-1 text-center text-[9px] text-gold">⚠ {warnings.length} layer berpotensi menutup wajah/UI</span>}
    </div></div>
    <div className="mx-auto mt-3 flex w-full max-w-xl items-center justify-center gap-3 rounded-lg border border-border-soft bg-navy-2/70 px-3 py-2"><button type="button" onClick={toggle} aria-label={playing ? 'Pause' : 'Play'} className="text-cyan-soft"><Icon name={playing ? 'pause' : 'play'} className="h-4 w-4"/></button><span className="font-mono text-[10px] text-white/60">{fmt(currentTime)} / {fmt(duration)}</span><span className="rounded border border-border px-1.5 py-0.5 font-mono text-[9px] text-white/35">1080×1920</span></div>
  </main>
}
function SafeZones() { return <div className="pointer-events-none absolute inset-0" aria-hidden><div className="absolute inset-x-[8%] top-[7%] h-[12%] rounded border border-dashed border-gold/45"><span className="absolute left-1 top-0.5 font-mono text-[7px] uppercase text-gold/80">Hook</span></div><div className="absolute inset-x-[8%] bottom-[14%] h-[18%] rounded border border-dashed border-cyan/45"><span className="absolute bottom-0.5 left-1 font-mono text-[7px] uppercase text-cyan-soft/80">Subtitle</span></div><div className="absolute bottom-[8%] right-[4%] h-[44%] w-[14%] rounded border border-dashed border-magenta/35"><span className="absolute right-1 top-0.5 font-mono text-[7px] uppercase text-magenta/70">UI</span></div></div> }

function Inspector({ active, activeTool, props, calib, calibBusy, calibErr, run, action, layers, selectedLayerId, onSelectLayer, onAddLayer, onUpdateLayer, onDeleteLayer, onDuplicateLayer, onUploadLayer, onRestoreVersion, onSaveLayers, onRenderLayers, layerDirty, layerState, layerMessage, autoSave, onAutoSave, previewQuality, onPreviewQuality }) {
  const layerProps={layers,selectedLayerId,onSelectLayer,onAddLayer,onUpdateLayer,onDeleteLayer,onDuplicateLayer,onUploadLayer,onRestoreVersion,onSaveLayers,onRenderLayers,layerDirty,layerState,layerMessage,autoSave,onAutoSave,previewQuality,onPreviewQuality}
  return <aside className="border-t border-border-soft bg-navy-2/45 lg:min-h-0 lg:overflow-y-auto lg:border-l lg:border-t-0"><div className="border-b border-border-soft px-4 py-3"><Micro className="text-[9px]">ACTIVE TOOL</Micro><div className="mt-1 flex items-center gap-2 text-sm font-semibold text-white"><Icon name={active.icon} className="h-4 w-4 text-cyan-soft"/> {active.label}</div><p className="mt-1 text-xs text-white/40">{active.hint}</p></div><div className="space-y-4 p-4">
    {activeTool === 'trim' && <Trim clip={props.clip} action={action}/>}
    {activeTool === 'audio' && <Audio props={props} run={run}/>}
    {activeTool === 'captions' && <Captions props={props} action={action}/>}
    {activeTool === 'layout' && <Layout clip={props.clip} action={action}/>}
    {activeTool === 'broll' && <Broll action={action} {...layerProps}/>}
    {activeTool === 'effects' && <Effects props={props} run={run}/>}
    {activeTool === 'insert' && <Insert action={action} {...layerProps}/>}
    {activeTool === 'brand' && <Brand action={action}/>}
    {activeTool === 'publish' && <Publish clip={props.clip} action={action} jobId={props.jobId} onClipUpdate={props.onClipUpdate}/>}
    {activeTool === 'repurpose' && <Repurpose props={props} action={action}/>}
    {activeTool === 'thumbnail' && <Thumbnail clip={props.clip} action={action}/>}
    {activeTool === 'insights' && <Insight props={props} calib={calib} busy={calibBusy} error={calibErr}/>}
  </div></aside>
}
function Trim({ clip, action }) { return <><div className="grid grid-cols-3 gap-2"><Metric label="Mulai" value={fmt(clip.start)}/><Metric label="Selesai" value={fmt(clip.end)}/><Metric label="Durasi" value={fmt(Math.max(0, Number(clip.end)-Number(clip.start)))}/></div><Info title="Non-destructive">Trim dan split scene tidak mengubah video sumber.</Info><Button variant="cyan" className="w-full" onClick={action}><Icon name="trim" className="h-4 w-4"/> Edit trim & scene</Button></> }
function Audio({ props, run }) { return <div className="grid grid-cols-2 gap-2"><Small icon="clean" label="Clean" onClick={() => run(props.onClean)}/><Small icon="sfx" label="SFX" onClick={() => run(props.onSfx)}/><Small icon="dub" label="Dub" onClick={() => run(props.onDub)}/><Small icon="mic" label="Voiceover" onClick={() => run(props.onVoiceover)}/></div> }
function Captions({ props, action }) { const lines = props.captionLines || []; return <><div className="max-h-52 space-y-2 overflow-y-auto rounded-lg border border-border-soft bg-black/15 p-3">{lines.length ? lines.map((line,i) => <div key={i} className="text-xs leading-relaxed text-white/65"><span className="mr-1 font-mono text-[9px] text-cyan-soft">{fmt(line.start)}–{fmt(line.end)}</span>{(line.text || '').trim()}</div>) : <span className="text-xs text-white/35">Tidak ada caption dalam rentang klip.</span>}</div><div className="grid grid-cols-2 gap-2"><Small icon={props.copiedCap ? 'check' : 'copy'} label="Copy" onClick={props.onCopyCaption}/><Small icon="edit" label="Edit style" onClick={action}/></div></> }
function Layout({ clip, action }) { const scenes = sceneList(clip); return <>{clip.layout_plan_edited ? <p className="font-mono text-[9px] uppercase text-signal">· LAYOUT EDITED</p> : null}<Info title="Portrait invariant">Preview dan hasil render tetap 9:16, 1080×1920, SAR 1:1.</Info><div className="rounded-lg border border-border-soft bg-black/15 p-3"><div className="font-mono text-[9px] uppercase text-white/35">Scene plan · {scenes.length || 'auto'}</div><div className="mt-2 flex flex-wrap gap-1.5">{scenes.length ? scenes.slice(0,8).map((s,i)=><span key={i} className="rounded bg-cyan/10 px-2 py-1 font-mono text-[9px] text-cyan-soft">{s.layout || s.type || `scene ${i+1}`}</span>) : <span className="text-xs text-white/35">Auto layout aktif</span>}</div></div><div className="grid grid-cols-2 gap-2"><Small icon="pin" label="Subject lock" onClick={action}/><Small icon="trim" label="Scene editor" onClick={action}/></div></> }
function Broll({ action, ...layerProps }) { return <><Info title="AI B-roll">Auto B-roll tetap diproses di backend. Slot timeline ini mengatur timing tanpa membebani browser.</Info><Button variant="ghost" className="w-full" onClick={()=>layerProps.onAddLayer('broll')}><Icon name="plus" className="h-4 w-4"/> Tambah slot B-roll</Button><LayerPanel {...layerProps}/><Button variant="cyan" className="w-full" onClick={action}><Icon name="broll" className="h-4 w-4"/> Cari footage otomatis</Button></> }
function Effects({ props, run }) { return <><div className="grid grid-cols-2 gap-2"><Small icon="fx" label="Video FX" onClick={() => run(props.onFx)}/><Small icon="sparkles" label="Enhance" onClick={() => run(props.onEnhance)}/><Small icon="swap" label="Face swap" onClick={() => run(props.onSwap)}/></div><Info title="Render scoped">Efek diterapkan pada klip ini dan melewati portrait guardian.</Info></> }
function Insert({ ...layerProps }) { const inputRef=useRef(null); const [mediaKind,setMediaKind]=useState('image'); return <><div className="grid grid-cols-3 gap-2"><Small icon="text" label="Text" onClick={()=>layerProps.onAddLayer('text')}/><Small icon="comments" label="Channel" onClick={()=>layerProps.onAddLayer('channel')}/><Small icon="broll" label="B-roll" onClick={()=>layerProps.onAddLayer('broll')}/></div><div className="flex gap-2"><select value={mediaKind} onChange={(e)=>setMediaKind(e.target.value)} aria-label="jenis media" className="rounded border border-border bg-navy-2 px-2 text-xs text-white"><option value="image">Gambar/logo</option><option value="broll">Video/B-roll</option><option value="audio">Audio</option></select><Button variant="ghost" className="flex-1" onClick={()=>inputRef.current?.click()}><Icon name="stack" className="h-4 w-4"/> Upload layer</Button><input ref={inputRef} type="file" className="hidden" accept={mediaKind==='image'?'image/png,image/jpeg,image/webp':mediaKind==='audio'?'audio/*':'video/*'} onChange={(e)=>{const file=e.target.files?.[0];if(file)layerProps.onUploadLayer(file,mediaKind);e.target.value=''}}/></div><LayerPanel {...layerProps}/></> }
function LayerPanel({layers,selectedLayerId,onSelectLayer,onUpdateLayer,onDeleteLayer,onDuplicateLayer,onRestoreVersion,onSaveLayers,onRenderLayers,layerDirty,layerState,layerMessage,autoSave,onAutoSave,previewQuality,onPreviewQuality}) {
  const selected=layers.find((layer)=>layer.id===selectedLayerId)
  return <div className="space-y-3">
    <div className="max-h-32 space-y-1 overflow-y-auto rounded-lg border border-border-soft bg-black/15 p-2">{layers.length?layers.map((layer)=><button key={layer.id} type="button" onClick={()=>onSelectLayer(layer.id)} className={`flex w-full items-center justify-between rounded px-2 py-1.5 text-left text-xs ${selectedLayerId===layer.id?'bg-cyan/15 text-cyan-soft':'text-white/55 hover:bg-white/5'}`}><span className="truncate">{layer.label}</span><span className="font-mono text-[8px]">{fmt(layer.start)}–{fmt(layer.end)}</span></button>):<span className="block p-2 text-xs text-white/35">Belum ada layer tambahan.</span>}</div>
    {selected&&<div className="space-y-2 rounded-lg border border-cyan/20 bg-cyan/5 p-3">
      {(selected.kind==='text'||selected.kind==='channel')&&<input value={selected.text||''} onChange={(e)=>onUpdateLayer(selected.id,{text:e.target.value,label:e.target.value||selected.kind})} aria-label="teks layer" className="w-full rounded border border-border bg-navy-2 px-2 py-1.5 text-xs text-white"/>}
      <div className="grid grid-cols-2 gap-2"><LayerNumber label="Mulai" value={selected.start} min={0} step={.1} onChange={(value)=>onUpdateLayer(selected.id,{start:value})}/><LayerNumber label="Selesai" value={selected.end} min={.1} step={.1} onChange={(value)=>onUpdateLayer(selected.id,{end:value})}/></div>
      <LayerRange label="Posisi X" value={selected.x} onChange={(value)=>onUpdateLayer(selected.id,{x:value})}/><LayerRange label="Posisi Y" value={selected.y} onChange={(value)=>onUpdateLayer(selected.id,{y:value})}/><LayerRange label="Lebar" value={selected.width} min={.05} onChange={(value)=>onUpdateLayer(selected.id,{width:value})}/><LayerRange label="Opacity" value={selected.opacity} onChange={(value)=>onUpdateLayer(selected.id,{opacity:value})}/>
      <LayerNumber label="Rotasi" value={selected.rotation||0} min={-180} step={1} onChange={(value)=>onUpdateLayer(selected.id,{rotation:value})}/><label className="block text-[9px] text-white/40">Animasi<select value={selected.animation||'none'} onChange={(e)=>onUpdateLayer(selected.id,{animation:e.target.value})} className="mt-1 w-full rounded border border-border bg-navy-2 px-2 py-1 text-xs text-white"><option value="none">None</option><option value="fade">Fade</option><option value="pop">Pop</option><option value="slide">Slide</option><option value="zoom">Zoom</option></select></label>
      {selected.kind==='audio'&&<><LayerRange label="Volume" value={selected.volume??.3} max={1.5} onChange={(value)=>onUpdateLayer(selected.id,{volume:value})}/><label className="flex items-center gap-2 text-[9px] text-white/50"><input type="checkbox" checked={selected.keep_original!==false} onChange={(e)=>onUpdateLayer(selected.id,{keep_original:e.target.checked})} className="accent-cyan"/> Pertahankan audio asli</label></>}
      <div className="grid grid-cols-4 gap-1"><button type="button" onClick={()=>onUpdateLayer(selected.id,{visible:!selected.visible})} className="rounded border border-border px-1 py-1 text-[9px] text-white/60">{selected.visible?'Hide':'Show'}</button><button type="button" onClick={()=>onUpdateLayer(selected.id,{locked:!selected.locked})} className="rounded border border-border px-1 py-1 text-[9px] text-white/60">{selected.locked?'Unlock':'Lock'}</button><button type="button" onClick={()=>onDuplicateLayer(selected.id)} className="rounded border border-border px-1 py-1 text-[9px] text-white/60">Duplicate</button><button type="button" onClick={()=>onDeleteLayer(selected.id)} className="rounded border border-danger/40 px-1 py-1 text-[9px] text-danger">Delete</button></div>
    </div>}
    <div className="grid grid-cols-3 gap-2 text-[9px] text-white/50"><label className="flex items-center gap-2"><input type="checkbox" checked={autoSave} onChange={(e)=>onAutoSave(e.target.checked)} className="accent-cyan"/> Autosave</label><label>Preview <select value={previewQuality} onChange={(e)=>onPreviewQuality(e.target.value)} className="ml-1 rounded border border-border bg-navy-2 px-1 py-1 text-white"><option value="eco">Eco</option><option value="full">Full</option></select></label><button type="button" onClick={onRestoreVersion} className="rounded border border-border px-1">Restore version</button></div>
    <div className="text-[10px] text-white/35">{layerMessage}</div><div className="grid grid-cols-2 gap-2"><Button variant="ghost" disabled={!layerDirty||layerState==='saving'} onClick={onSaveLayers}><Icon name="check" className="h-3.5 w-3.5"/> Save</Button><Button variant="cyan" disabled={layerDirty||layerState==='saving'||!layers.length} onClick={onRenderLayers}>{layerState==='saving'?<Spinner className="h-3.5 w-3.5"/>:<Icon name="publish" className="h-3.5 w-3.5"/>} Render</Button></div>
  </div>
}
function LayerNumber({label,value,min,step,onChange}) { return <label className="text-[9px] text-white/40">{label}<input type="number" min={min} step={step} value={value} onChange={(e)=>onChange(Number(e.target.value))} className="mt-1 w-full rounded border border-border bg-navy-2 px-2 py-1 text-xs text-white"/></label> }
function LayerRange({label,value,min=0,max=1,onChange}) { return <label className="block text-[9px] text-white/40"><span className="flex justify-between"><span>{label}</span><span>{Math.round(value*100)}%</span></span><input type="range" min={min} max={max} step={.01} value={value} onChange={(e)=>onChange(Number(e.target.value))} className="w-full accent-cyan"/></label> }
function Brand({ action }) { return <><Info title="Brand workspace">Gunakan font, warna, caption style, dan watermark yang konsisten.</Info><OptionRows rows={[['Caption style','Workspace default'],['Safe-zone','Aktif'],['Output','1080×1920']]}/><Button variant="cyan" className="w-full" onClick={action}><Icon name="paint" className="h-4 w-4"/> Atur caption & brand</Button></> }
function Publish({ clip, action, jobId, onClipUpdate }) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const apply = async (next) => {
    setBusy(true); setMsg('')
    try {
      const note = next === 'changes_requested' ? (window.prompt('Catatan revisi (opsional):', clip.approval_note || '') ?? clip.approval_note ?? '') : (clip.approval_note || '')
      if (next === 'changes_requested' && note === null) { setBusy(false); return }
      const res = await setClipApproval(jobId, clip.index, next, note)
      onClipUpdate?.({ index: clip.index, approval_status: res.approval_status, approval_note: res.note ?? note })
      setMsg(next === 'approved' ? '✅ Approved' : next ? '✕ Revisi tersimpan' : '↺ Status dihapus')
    } catch (e) { setMsg(`⚠️ ${e.message}`) }
    finally { setBusy(false) }
  }
  const pill = clip.approval_status === 'approved' ? 'APPROVED' : clip.approval_status === 'changes_requested' ? 'REVISI' : clip.approval_status === 'pending' ? 'PENDING' : ''
  return <><div className="grid grid-cols-3 gap-2">{['youtube','tiktok','instagram'].map((platform)=><div key={platform} className="rounded-lg border border-border-soft bg-black/15 p-2 text-center text-[10px] uppercase text-white/55">{platform}</div>)}</div><Info title="Review status">{pill ? `Status klip: ${pill}` : 'Belum memiliki status approval.'}{clip.approval_note ? ` — “${clip.approval_note.slice(0, 60)}”` : ''}</Info><div className="grid grid-cols-3 gap-2"><button type="button" disabled={busy} onClick={() => apply('approved')} className="rounded border border-signal/40 bg-signal/10 px-2 py-1.5 text-xs text-signal disabled:opacity-40">✓ Setujui</button><button type="button" disabled={busy} onClick={() => apply('changes_requested')} className="rounded border border-warn/40 bg-warn/10 px-2 py-1.5 text-xs text-warn disabled:opacity-40">✕ Revisi</button><button type="button" disabled={busy || !clip.approval_status} onClick={() => apply('')} className="rounded border border-border px-2 py-1.5 text-xs text-white/50 disabled:opacity-40">↺ Reset</button></div>{msg && <p className="text-xs text-white/50">{msg}</p>}<Button variant="cyan" className="w-full" onClick={action}><Icon name="publish" className="h-4 w-4"/> Atur publish</Button></> }
function Repurpose({ props, action }) { return <><OptionRows rows={[['Blog','Long-form'],['X / Threads','Short post'],['LinkedIn','Professional'],['Instagram','Caption']]}/><div className="grid grid-cols-2 gap-2"><Small icon={props.copiedCap ? 'check' : 'copy'} label={props.copiedCap ? 'Tersalin' : 'Copy caption'} onClick={props.onCopyCaption}/><Small icon="repurpose" label="Generate" onClick={action}/></div></> }
function Thumbnail({ clip, action }) { return <>{clip.thumbnail_url ? <img src={resolveAsset(clip.thumbnail_url)} alt="Thumbnail klip" className="aspect-[9/16] max-h-52 w-full rounded-lg border border-border-soft bg-black object-contain"/> : <Info title="Belum ada thumbnail">Buat thumbnail portrait dari frame aktual klip.</Info>}<OptionRows rows={[['Format','Shorts 9:16'],['Source','Rendered clip'],['Safe-zone','Aktif']]}/><Button variant="cyan" className="w-full" onClick={action}><Icon name="thumbnail" className="h-4 w-4"/> Buka Thumbnail Studio</Button></> }
function Insight({ props, calib, busy, error }) { const quality=props.clip.quality_breakdown||{}; const order=['hook','context','payoff','emotion','technical_quality']; const entries=[...Object.entries(quality)].sort((a,b)=>{const ia=order.indexOf(a[0]); const ib=order.indexOf(b[0]); return (ia<0?99:ia)-(ib<0?99:ib)}); return <>{Object.keys(quality).length>0 && <Section title="Clip quality breakdown"><div className="space-y-2">{entries.map(([key,value])=><div key={key}><div className="mb-1 flex justify-between font-mono text-[9px] uppercase text-white/45"><span>{key.replaceAll('_',' ')}</span><span>{Math.round(Number(value)||0)}</span></div><div className="h-1.5 overflow-hidden rounded bg-black/30"><div className="h-full rounded bg-cyan" style={{width:`${Math.max(0,Math.min(100,Number(value)||0))}%`}}/></div></div>)}</div></Section>}<Section title="Published analytics">{props.statsBusy && <Loading text="Mengambil analytics…"/>}{!props.statsBusy && props.statsErr && <p className="text-xs text-danger">{props.statsErr}</p>}{!props.statsBusy && !props.statsErr && (!props.stats || !props.stats.length) && <p className="text-xs text-white/35">Belum ada post ter-publish untuk klip ini.</p>}{!props.statsBusy && props.stats?.map((a)=><div key={`${a.platform}-${a.post_id}`} className="rounded border border-border-soft p-2 text-xs text-white/65"><b className="uppercase text-cyan-soft">{a.platform}</b>{a.metrics && <div className="mt-1 flex flex-wrap gap-2 font-mono text-[9px]">{Object.entries(a.metrics).map(([k,v])=><span key={k}>{k} {count(v)}</span>)}</div>}</div>)}</Section><Section title="Score calibration">{busy && <Loading text="Menghitung calibration…"/>}{error && <p className="text-xs text-danger">{error}</p>}{!busy && !error && calib && <div className="grid grid-cols-4 gap-2"><Metric label="Sampel" value={calib.samples}/><Metric label="r" value={calib.pearson_r ?? '—'}/><Metric label="Bias" value={calib.mean_bias ?? '—'}/><Metric label="MAE" value={calib.mae ?? '—'}/></div>}{!busy && !error && !calib && <p className="text-xs text-white/35">Belum ada data calibration.</p>}</Section></> }
function Section({ title, children }) { return <section><div className="mb-2 font-mono text-[9px] uppercase tracking-wider text-white/40">{title}</div><div className="space-y-2">{children}</div></section> }
function Loading({ text }) { return <div className="flex items-center gap-2 text-xs text-white/40"><Spinner className="h-3 w-3"/>{text}</div> }
function Metric({label,value}) { return <div className="rounded border border-border-soft bg-black/15 p-2"><div className="font-mono text-[8px] uppercase text-white/35">{label}</div><div className="font-mono text-sm text-white/80">{value}</div></div> }
function Info({title,children}) { return <div className="rounded-lg border border-cyan/20 bg-cyan/5 p-3"><div className="font-mono text-[9px] uppercase text-cyan-soft">{title}</div><p className="mt-1 text-xs leading-relaxed text-white/50">{children}</p></div> }
function Small({icon,label,onClick}) { return <button type="button" onClick={onClick} className="flex items-center gap-2 rounded-lg border border-border p-2.5 text-left text-xs text-white/70 hover:border-cyan/50 hover:bg-cyan/5"><Icon name={icon} className="h-4 w-4 text-cyan-soft"/>{label}</button> }
function OptionRows({ rows }) { return <div className="divide-y divide-border-soft overflow-hidden rounded-lg border border-border-soft bg-black/15">{rows.map(([label,value])=><div key={label} className="flex items-center justify-between gap-3 px-3 py-2 text-xs"><span className="text-white/40">{label}</span><span className="text-right text-white/70">{value}</span></div>)}</div> }

function Timeline({clip,scenes,captions,layers,selectedLayerId,onSelectLayer,progress,current,duration,selected,onSelect,onSeek,onOpenEditor,onSplit,onDelete,onLayout,onSave,dirty,planState,planMessage,wave}) {
  const sceneBlocks=(scenes.length ? scenes : [{start:0,end:duration,layout:'auto'}]).map((scene,index)=>({
    start:Number(scene.start)||0, end:Number(scene.end)||duration,
    label:scene.layout||scene.strategy||'auto', index,
  }))
  const captionBlocks=captions.map((caption,index)=>({
    start:Math.max(0,(Number(caption.start)||0)-(Number(clip.start)||0)),
    end:Math.max(0,(Number(caption.end)||0)-(Number(clip.start)||0)),
    label:(caption.text||'caption').trim(), index,
  })).filter((block)=>block.end>0&&block.start<duration)
  const layerBlocks=(layers||[]).map((layer,index)=>({start:layer.start,end:layer.end,label:layer.label,index,id:layer.id}))
  return <section className="h-64 shrink-0 overflow-y-auto border-t border-border-soft bg-navy-2/75 px-4 py-3">
    <div className="mb-2 flex items-center justify-between gap-3"><Micro className="text-[9px]">TIMELINE · klik untuk seek</Micro><div className="flex items-center gap-2"><button type="button" onClick={onOpenEditor} className="rounded border border-border px-2 py-1 font-mono text-[8px] uppercase text-white/45 hover:border-cyan/50 hover:text-cyan-soft">Advanced</button><span className="font-mono text-[9px] text-white/35">{fmt(current)} / {fmt(duration)}</span></div></div>
    <div className="relative space-y-1.5">
      <TimelineTrack label="VIDEO" tone="bg-cyan/25" blocks={sceneBlocks} duration={duration} selected={selected} type="scene" onSelect={onSelect} onSeek={onSeek}/>
      <TimelineTrack label="CAPTION" tone="bg-magenta/25" blocks={captionBlocks} duration={duration} selected={selected} type="caption" onSelect={onSelect} onSeek={onSeek}/>
      <TimelineTrack label="LAYERS" tone="bg-signal/20" blocks={layerBlocks} duration={duration} selected={selectedLayerId?{type:'layer',index:layerBlocks.findIndex((block)=>block.id===selectedLayerId)}:null} type="layer" onSelect={(block)=>onSelectLayer(block.id)} onSeek={onSeek}/>
      <TimelineTrack label="AUDIO" tone="bg-gold/20" blocks={[{start:0,end:duration,label:'original',index:0}]} duration={duration} selected={selected} type="audio" onSelect={onSelect} onSeek={onSeek}/>
      <div className="pointer-events-none absolute inset-y-0 z-20 w-px bg-white shadow-[0_0_8px_white]" style={{left:`calc(4rem + (100% - 4rem) * ${progress/100})`}}/>
    </div>
    <div className="mt-2 flex min-h-6 items-center gap-2 pl-16 font-mono text-[9px] text-white/40">{selected?.type==='scene' ? <><select value={selected.label} onChange={(event)=>onLayout(selected.index,event.target.value)} aria-label="layout scene terpilih" className="rounded border border-border bg-navy-2 px-1.5 py-1 text-[9px] text-white"><option value="track">Talking head</option><option value="general">Group / full width</option><option value="split">Split</option><option value="panel">Panel</option><option value="center">Center</option><option value="gameplay">Gameplay</option></select><button type="button" onClick={onSplit} className="rounded border border-cyan/35 px-2 py-1 text-cyan-soft">+ Split</button><button type="button" disabled={scenes.length<=1} onClick={()=>onDelete(selected.index)} className="rounded border border-danger/35 px-2 py-1 text-danger disabled:opacity-30">Delete</button></> : selected ? `${selected.type.toUpperCase()} ${selected.index+1} · ${selected.label} · ${fmt(selected.start)}–${fmt(selected.end)}` : 'Pilih scene atau caption untuk melihat detail.'}<span className="ml-auto truncate text-white/30">{planMessage}</span><button type="button" disabled={!dirty||planState==='saving'} onClick={onSave} className="rounded border border-signal/40 bg-signal/10 px-2 py-1 uppercase text-signal disabled:opacity-40">{planState==='saving'?'Saving…':'Save'}</button></div>
    {wave?.peaks?.length>0&&<div className="mt-2 pl-16"><Waveform peaks={wave.peaks} duration={Number(wave.duration)||duration} start={Number(clip.start)||0} end={Number(clip.end)||duration} segments={[]} minLen={.1} maxLen={Math.max(180,duration)} onSeek={(sourceTime)=>onSeek(sourceTime-(Number(clip.start)||0))} onChange={()=>{}}/></div>}
  </section>
}
function TimelineTrack({label,tone,blocks,duration,selected,type,onSelect,onSeek}) {
  const clickTrack=(event)=>{if(event.target!==event.currentTarget)return;const rect=event.currentTarget.getBoundingClientRect();onSeek(((event.clientX-rect.left)/rect.width)*duration)}
  return <div className="flex h-6 items-center"><span className="w-16 shrink-0 font-mono text-[8px] text-white/35">{label}</span><div className="relative h-full flex-1 overflow-hidden rounded bg-black/25" onClick={clickTrack}>{blocks.map((block)=><button key={`${type}-${block.index}`} type="button" title={`${block.label} · ${fmt(block.start)}–${fmt(block.end)}`} aria-label={`${type} ${block.index+1}: ${block.label}`} onClick={()=>{onSelect({...block,type});onSeek(block.start)}} className={`absolute inset-y-0 overflow-hidden border px-1 text-left font-mono text-[8px] transition ${tone} ${selected?.type===type&&selected?.index===block.index?'z-10 border-white/80 text-white':'border-white/10 text-white/55 hover:border-white/45'}`} style={{left:`${duration?Math.max(0,block.start/duration*100):0}%`,width:`${duration?Math.max(1,(Math.min(duration,block.end)-Math.max(0,block.start))/duration*100):100}%`}}><span className="block truncate">{block.label}</span></button>)}</div></div>
}
function MobileBar({active,onSelect}) { const items=[...TOOLS,...INSIGHTS]; return <nav aria-label="Mobile editor tools" className="flex shrink-0 gap-1 overflow-x-auto border-t border-border-soft bg-navy-2 px-2 py-2 lg:hidden">{items.map((t)=><button key={t.id} type="button" onClick={()=>onSelect(t.id)} aria-label={t.label} className={`flex min-w-[3.5rem] flex-col items-center gap-1 rounded-md px-2 py-1 text-[8px] uppercase ${active===t.id?'bg-cyan/15 text-cyan-soft':'text-white/40'}`}><Icon name={t.icon} className="h-4 w-4"/><span className="max-w-12 truncate">{t.label}</span></button>)}</nav> }
function toggleVideo(ref) { const v=ref.current; if(!v)return; if(v.paused)v.play().catch(()=>{});else v.pause() }
function seekVideo(ref,seconds,setCurrentTime) { const video=ref.current; if(video) video.currentTime=seconds; setCurrentTime(seconds) }
function closeWithGuard(onClose,dirtyRef) { if(dirtyRef.current&&!window.confirm('Perubahan layout belum disimpan. Keluar tanpa menyimpan?'))return false; onClose(); return true }
function sceneList(clip) { const plan=clip.layout_plan||clip.scene_layouts||[]; if(Array.isArray(plan))return plan; if(Array.isArray(plan.scenes))return plan.scenes; return [] }
function normalizeScene(scene) { return {start:Number(scene.start)||0,end:Number(scene.end)||0,layout:scene.layout||scene.strategy||'track',strategy:scene.layout||scene.strategy||'track'} }
function initialScenes(clip,duration) { const scenes=sceneList(clip).map(normalizeScene).filter((scene)=>scene.end-scene.start>=.5); return scenes.length?scenes:[{start:0,end:duration,layout:'track',strategy:'track'}] }
function cloneScenes(scenes) { return scenes.map((scene)=>({...scene})) }
function normalizeLayers(layers,duration) { const num=(value,fallback)=>Number.isFinite(Number(value))?Number(value):fallback; return (Array.isArray(layers)?layers:[]).map((layer,index)=>({id:String(layer.id||`layer-${index+1}`),kind:layer.kind||'text',label:layer.label||layer.kind||'Layer',text:layer.text||'',start:Math.max(0,num(layer.start,0)),end:Math.min(duration,Math.max(.1,num(layer.end,duration))),x:Math.max(0,Math.min(1,num(layer.x,.5))),y:Math.max(0,Math.min(1,num(layer.y,.5))),width:Math.max(.05,Math.min(1,num(layer.width,.7))),opacity:Math.max(0,Math.min(1,num(layer.opacity,1))),rotation:Math.max(-180,Math.min(180,num(layer.rotation,0))),volume:Math.max(0,Math.min(1.5,num(layer.volume,.3))),visible:layer.visible!==false,locked:!!layer.locked,color:layer.color||'#FFFFFF',asset_url:layer.asset_url||'',media_path:layer.media_path||'',animation:layer.animation||'none',transition:layer.transition||'fade',keep_original:layer.keep_original!==false})) }
function cloneLayers(layers) { return layers.map((layer)=>({...layer})) }
function snapUnit(value) { const clamped=Math.max(0,Math.min(1,value)); const target=[.1,.5,.9].find((point)=>Math.abs(point-clamped)<.025); return target??clamped }
function rememberVersion(jobId,clipIdx,layers) { try { const key=`klipin-layer-versions:${jobId}:${clipIdx}`; const versions=JSON.parse(localStorage.getItem(key)||'[]'); versions.push({saved_at:Date.now(),layers}); localStorage.setItem(key,JSON.stringify(versions.slice(-10))) } catch { /* storage may be unavailable */ } }
function fmt(value) { const s=Math.max(0,Number(value)||0); return `${Math.floor(s/60)}:${Math.floor(s%60).toString().padStart(2,'0')}` }
function count(value) { const n=Number(value)||0; if(n>=1e6)return `${(n/1e6).toFixed(1)}M`;if(n>=1e3)return `${(n/1e3).toFixed(1)}K`;return String(n) }

export const PLATFORM_DOTS={youtube:'#FF0033',tiktok:'#69C9D0',instagram:'#E1306C'}
export const METRIC_ICONS={views:'views',likes:'thumb',comments:'comments',shares:'repurpose',reach:'radio'}
