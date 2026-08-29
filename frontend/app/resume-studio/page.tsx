'use client'

import { ChangeEvent, useEffect, useMemo, useRef, useState } from 'react'
import {
  Check, ChevronDown, ChevronUp, Download, Eye, EyeOff, Plus, RefreshCw, Trash2, UploadCloud,
} from 'lucide-react'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import CopyButton from '@/components/CopyButton'
import { api, ResumeDocument, ResumeItem, ResumeSection, ResumeSectionType } from '@/lib/api'
import { computeAtsScore } from '@/lib/ats-score'

function base64ToBlob(base64: string, type: string) {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes], { type })
}

function errorMessage(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback
}

function uid() {
  return Math.random().toString(36).slice(2, 10)
}

function hasMeaningfulContent(doc: ResumeDocument): boolean {
  if (doc.personal.full_name.trim()) return true
  return doc.sections.some(section =>
    section.items.some(item =>
      Object.entries(item).some(([key, value]) => {
        if (key === 'bullets' || key === 'items') return Array.isArray(value) && value.some(v => v && v.trim())
        return typeof value === 'string' && value.trim().length > 0
      })
    )
  )
}

const SECTION_LABELS: Record<ResumeSectionType, string> = {
  summary: 'Professional Summary',
  education: 'Education',
  experience: 'Experience',
  projects: 'Projects',
  skills: 'Technical Skills',
  certifications: 'Certifications',
  custom: 'Custom Section',
}

type FieldConfig = { key: keyof ResumeItem; label: string }

const ITEM_FIELDS: Record<ResumeSectionType, FieldConfig[]> = {
  summary: [],
  education: [
    { key: 'school', label: 'School' }, { key: 'location', label: 'Location' },
    { key: 'degree', label: 'Degree' }, { key: 'dates', label: 'Dates' },
  ],
  experience: [
    { key: 'title', label: 'Title' }, { key: 'company', label: 'Company' },
    { key: 'location', label: 'Location' }, { key: 'dates', label: 'Dates' },
  ],
  projects: [
    { key: 'name', label: 'Name' }, { key: 'tech', label: 'Technologies' },
    { key: 'dates', label: 'Dates' }, { key: 'link', label: 'Link' },
  ],
  skills: [{ key: 'category', label: 'Category' }],
  certifications: [
    { key: 'name', label: 'Name' }, { key: 'issuer', label: 'Issuer' }, { key: 'date', label: 'Date' },
  ],
  custom: [{ key: 'heading', label: 'Heading' }],
}

const LIST_FIELD: Record<ResumeSectionType, 'bullets' | 'items' | null> = {
  summary: null, education: 'bullets', experience: 'bullets', projects: 'bullets',
  skills: 'items', certifications: null, custom: 'bullets',
}
const LIST_LABEL: Record<ResumeSectionType, string> = {
  summary: '', education: 'bullet', experience: 'bullet', projects: 'bullet',
  skills: 'skill', certifications: '', custom: 'bullet',
}

function emptyItem(type: ResumeSectionType): ResumeItem {
  switch (type) {
    case 'summary': return { text: '' }
    case 'education': return { school: '', location: '', degree: '', dates: '', bullets: [] }
    case 'experience': return { title: '', company: '', location: '', dates: '', bullets: [] }
    case 'projects': return { name: '', tech: '', dates: '', link: '', bullets: [] }
    case 'skills': return { category: '', items: [] }
    case 'certifications': return { name: '', issuer: '', date: '' }
    case 'custom': return { heading: '', bullets: [] }
  }
}

function itemCountLabel(section: ResumeSection) {
  if (section.type === 'summary') return section.items[0]?.text ? 'Written' : 'Empty'
  const n = section.items.length
  return `${n} item${n === 1 ? '' : 's'}`
}

export default function ResumeStudioPage() {
  const [doc, setDoc] = useState<ResumeDocument | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [expanded, setExpanded] = useState<string | null>(null)
  const [showLatex, setShowLatex] = useState(false)

  const [importing, setImporting] = useState(false)
  const [importError, setImportError] = useState('')

  const [latex, setLatex] = useState('')
  const [pdfBase64, setPdfBase64] = useState<string | null>(null)
  const [pdfUrl, setPdfUrl] = useState<string | null>(null)
  const [filename, setFilename] = useState<string | null>(null)
  const [compileError, setCompileError] = useState<string | null>(null)
  const [compiling, setCompiling] = useState(false)

  // Debounced autosave/compile reads from this ref rather than the `doc`
  // state closure, so a burst of edits inside the 800ms window always
  // saves/compiles the latest document instead of a stale snapshot.
  const docRef = useRef<ResumeDocument | null>(null)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  // Live, deterministic, zero-latency — no network round-trip, so it updates
  // on every keystroke rather than waiting for the debounced compile.
  const ats = useMemo(() => (doc ? computeAtsScore(doc) : null), [doc])

  useEffect(() => {
    let cancelled = false
    api.resumes.document.get()
      .then(loaded => {
        if (cancelled) return
        setDoc(loaded)
        docRef.current = loaded
        compileNow(loaded)
      })
      .catch(err => setLoadError(errorMessage(err, 'Could not load your resume.')))
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!pdfBase64) { setPdfUrl(null); return }
    const url = URL.createObjectURL(base64ToBlob(pdfBase64, 'application/pdf'))
    setPdfUrl(url)
    return () => URL.revokeObjectURL(url)
  }, [pdfBase64])

  async function compileNow(document: ResumeDocument) {
    setCompiling(true)
    try {
      const result = await api.resumes.document.compile(document)
      setLatex(result.latex_content)
      setPdfBase64(result.pdf_base64)
      setFilename(result.filename)
      setCompileError(result.compile_error || null)
    } catch (err) {
      setCompileError(errorMessage(err, 'Compilation failed.'))
    } finally {
      setCompiling(false)
    }
  }

  function updateDoc(next: ResumeDocument) {
    setDoc(next)
    docRef.current = next
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => {
      const current = docRef.current
      if (!current) return
      api.resumes.document.save(current).catch(() => {})
      compileNow(current)
    }, 800)
  }

  async function handleImport(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    if (docRef.current && hasMeaningfulContent(docRef.current)) {
      const proceed = window.confirm('Importing a new resume will replace your current sections. Continue?')
      if (!proceed) return
    }
    setImportError('')
    setImporting(true)
    try {
      await api.resumes.extractText(file)
      const fresh = await api.resumes.document.get()
      setDoc(fresh)
      docRef.current = fresh
      compileNow(fresh)
    } catch (err) {
      setImportError(errorMessage(err, 'Could not import that file.'))
    } finally {
      setImporting(false)
    }
  }

  function mapSection(sectionId: string, fn: (s: ResumeSection) => ResumeSection): ResumeDocument {
    const current = docRef.current as ResumeDocument
    return { ...current, sections: current.sections.map(s => (s.id === sectionId ? fn(s) : s)) }
  }

  function updatePersonal(key: keyof ResumeDocument['personal'], value: string) {
    const current = docRef.current as ResumeDocument
    updateDoc({ ...current, personal: { ...current.personal, [key]: value } })
  }

  function updateLink(index: number, key: 'label' | 'url', value: string) {
    const current = docRef.current as ResumeDocument
    const links = [...current.personal.links]
    links[index] = { ...links[index], [key]: value }
    updateDoc({ ...current, personal: { ...current.personal, links } })
  }

  function addLink() {
    const current = docRef.current as ResumeDocument
    updateDoc({ ...current, personal: { ...current.personal, links: [...current.personal.links, { label: '', url: '' }] } })
  }

  function removeLink(index: number) {
    const current = docRef.current as ResumeDocument
    updateDoc({ ...current, personal: { ...current.personal, links: current.personal.links.filter((_, i) => i !== index) } })
  }

  function toggleVisible(sectionId: string) {
    updateDoc(mapSection(sectionId, section => ({ ...section, visible: !section.visible })))
  }

  function updateSectionTitle(sectionId: string, title: string) {
    updateDoc(mapSection(sectionId, section => ({ ...section, title })))
  }

  function removeSection(sectionId: string) {
    const current = docRef.current as ResumeDocument
    updateDoc({ ...current, sections: current.sections.filter(s => s.id !== sectionId) })
  }

  function moveSection(sectionId: string, dir: -1 | 1) {
    const current = docRef.current as ResumeDocument
    const ordered = [...current.sections].sort((a, b) => a.order - b.order)
    const idx = ordered.findIndex(s => s.id === sectionId)
    const swapWith = idx + dir
    if (idx < 0 || swapWith < 0 || swapWith >= ordered.length) return
    const a = ordered[idx]
    const b = ordered[swapWith]
    const sections = current.sections.map(s => {
      if (s.id === a.id) return { ...s, order: b.order }
      if (s.id === b.id) return { ...s, order: a.order }
      return s
    })
    updateDoc({ ...current, sections })
  }

  function addSection(type: ResumeSectionType) {
    const current = docRef.current as ResumeDocument
    const maxOrder = current.sections.reduce((m, s) => Math.max(m, s.order), -1)
    const section: ResumeSection = {
      id: uid(), type, title: SECTION_LABELS[type], visible: true, order: maxOrder + 1,
      items: type === 'summary' ? [{ text: '' }] : [],
    }
    updateDoc({ ...current, sections: [...current.sections, section] })
  }

  function addItem(sectionId: string) {
    updateDoc(mapSection(sectionId, section => ({ ...section, items: [...section.items, emptyItem(section.type)] })))
  }

  function removeItem(sectionId: string, itemIndex: number) {
    updateDoc(mapSection(sectionId, section => ({ ...section, items: section.items.filter((_, i) => i !== itemIndex) })))
  }

  function updateItemField(sectionId: string, itemIndex: number, key: keyof ResumeItem, value: string) {
    updateDoc(mapSection(sectionId, section => {
      const items = [...section.items]
      while (items.length <= itemIndex) items.push(emptyItem(section.type))
      items[itemIndex] = { ...items[itemIndex], [key]: value }
      return { ...section, items }
    }))
  }

  function updateListValue(sectionId: string, itemIndex: number, listKey: 'bullets' | 'items', valueIndex: number, value: string) {
    updateDoc(mapSection(sectionId, section => {
      const items = [...section.items]
      const list = [...(items[itemIndex]?.[listKey] || [])]
      list[valueIndex] = value
      items[itemIndex] = { ...items[itemIndex], [listKey]: list }
      return { ...section, items }
    }))
  }

  function addListValue(sectionId: string, itemIndex: number, listKey: 'bullets' | 'items') {
    updateDoc(mapSection(sectionId, section => {
      const items = [...section.items]
      items[itemIndex] = { ...items[itemIndex], [listKey]: [...(items[itemIndex]?.[listKey] || []), ''] }
      return { ...section, items }
    }))
  }

  function removeListValue(sectionId: string, itemIndex: number, listKey: 'bullets' | 'items', valueIndex: number) {
    updateDoc(mapSection(sectionId, section => {
      const items = [...section.items]
      items[itemIndex] = { ...items[itemIndex], [listKey]: (items[itemIndex]?.[listKey] || []).filter((_, i) => i !== valueIndex) }
      return { ...section, items }
    }))
  }

  function downloadPdf() {
    if (!pdfUrl) return
    const a = document.createElement('a')
    a.href = pdfUrl
    a.download = filename || 'resume.pdf'
    a.click()
  }

  const sortedSections = doc ? [...doc.sections].sort((a, b) => a.order - b.order) : []

  return (
    <RequireAuth>
      <AppShell>
        <section className="page-heading">
          <p className="eyebrow">RESUME STUDIO</p>
          <h1>Build your resume, section by section.</h1>
          <p>Edit structured sections on the left — your LaTeX source, compiled PDF, and ATS score all update live as you type.</p>
        </section>

        {loading && <div className="card-empty">Loading your resume…</div>}
        {loadError && <p className="form-error">{loadError}</p>}

        {doc && ats && (
          <div className="editor-panel ats-live-panel">
            <div className="editor-panel-head">
              <h2>Live ATS score</h2>
            </div>
            <div className="ats-score-row">
              <span className="ats-score-badge">{ats.score}<small>/100</small></span>
              <div style={{ flex: 1 }}>
                {ats.suggestions.length > 0 ? (
                  <ul className="suggestion-list">
                    {ats.suggestions.map(s => <li key={s}>{s}</li>)}
                  </ul>
                ) : (
                  <p className="muted-copy">Nice — no major gaps detected. Keep an eye on this as you keep editing.</p>
                )}
              </div>
            </div>
          </div>
        )}

        {doc && (
          <div className="editor-shell">
            <div className="editor-panel">
              <div className="editor-panel-head">
                <h2>Resume sections</h2>
                <label htmlFor="resume-import-file" className="outline-button upload-trigger">
                  <UploadCloud size={13} /> {importing ? 'Importing…' : 'Import resume'}
                </label>
                <input id="resume-import-file" type="file" accept=".pdf,.docx,.txt" hidden onChange={handleImport} disabled={importing} />
              </div>
              {importError && <p className="form-error" style={{ marginBottom: 10 }}>{importError}</p>}

              <div className="editor-section-card">
                <div className="editor-section-head" onClick={() => setExpanded(expanded === 'personal' ? null : 'personal')}>
                  <div className="editor-section-title">
                    <strong>Personal details</strong>
                    <span>Contact information</span>
                  </div>
                  {expanded === 'personal' ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                </div>
                {expanded === 'personal' && (
                  <div className="editor-section-body">
                    <div className="editor-personal-grid">
                      <label>Full name<input value={doc.personal.full_name} onChange={e => updatePersonal('full_name', e.target.value)} /></label>
                      <label>Headline<input value={doc.personal.headline} onChange={e => updatePersonal('headline', e.target.value)} /></label>
                      <label>Email<input value={doc.personal.email} onChange={e => updatePersonal('email', e.target.value)} /></label>
                      <label>Phone<input value={doc.personal.phone} onChange={e => updatePersonal('phone', e.target.value)} /></label>
                      <label>Location<input value={doc.personal.location} onChange={e => updatePersonal('location', e.target.value)} /></label>
                    </div>
                    <div className="editor-bullets">
                      {doc.personal.links.map((link, i) => (
                        <div key={i} className="editor-bullet-row">
                          <input placeholder="Label (e.g. GitHub)" value={link.label} onChange={e => updateLink(i, 'label', e.target.value)} />
                          <input placeholder="URL" value={link.url} onChange={e => updateLink(i, 'url', e.target.value)} />
                          <button type="button" className="editor-icon-btn" onClick={() => removeLink(i)} title="Remove link"><Trash2 size={13} /></button>
                        </div>
                      ))}
                      <button type="button" className="editor-add-link" onClick={addLink}><Plus size={12} /> Add link</button>
                    </div>
                  </div>
                )}
              </div>

              <div className="editor-section-list" style={{ marginTop: 10 }}>
                {sortedSections.map((section, idx) => (
                  <div key={section.id} className={`editor-section-card${section.visible ? '' : ' hidden'}`}>
                    <div className="editor-section-head" onClick={() => setExpanded(expanded === section.id ? null : section.id)}>
                      <div className="editor-reorder-btns">
                        <button
                          type="button" className="editor-icon-btn" disabled={idx === 0}
                          onClick={e => { e.stopPropagation(); moveSection(section.id, -1) }} title="Move up"
                        ><ChevronUp size={13} /></button>
                        <button
                          type="button" className="editor-icon-btn" disabled={idx === sortedSections.length - 1}
                          onClick={e => { e.stopPropagation(); moveSection(section.id, 1) }} title="Move down"
                        ><ChevronDown size={13} /></button>
                      </div>
                      <div className="editor-section-title">
                        <input
                          value={section.title}
                          onChange={e => updateSectionTitle(section.id, e.target.value)}
                          onClick={e => e.stopPropagation()}
                          style={{ border: 'none', background: 'transparent', padding: 0, fontWeight: 600, fontSize: 13, width: '100%' }}
                        />
                        <span>{itemCountLabel(section)}</span>
                      </div>
                      <button
                        type="button" className="editor-icon-btn"
                        onClick={e => { e.stopPropagation(); toggleVisible(section.id) }}
                        title={section.visible ? 'Hide section' : 'Show section'}
                      >
                        {section.visible ? <Eye size={14} /> : <EyeOff size={14} />}
                      </button>
                      <button
                        type="button" className="editor-icon-btn"
                        onClick={e => { e.stopPropagation(); removeSection(section.id) }} title="Remove section"
                      ><Trash2 size={14} /></button>
                      {expanded === section.id ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                    </div>

                    {expanded === section.id && (
                      <div className="editor-section-body">
                        {section.type === 'summary' ? (
                          <textarea
                            value={section.items[0]?.text || ''}
                            onChange={e => updateItemField(section.id, 0, 'text', e.target.value)}
                            placeholder="A 2-3 sentence career pitch…"
                          />
                        ) : (
                          <>
                            {section.items.map((item, itemIndex) => {
                              const listKey = LIST_FIELD[section.type]
                              return (
                                <div key={itemIndex} className="editor-item-card">
                                  <div className="editor-item-head">
                                    <span>Item {itemIndex + 1}</span>
                                    <button type="button" className="editor-remove-link" onClick={() => removeItem(section.id, itemIndex)}>Remove</button>
                                  </div>
                                  <div className="editor-field-row">
                                    {ITEM_FIELDS[section.type].map(field => (
                                      <label key={field.key} className="editor-field">
                                        {field.label}
                                        <input
                                          value={(item[field.key] as string) || ''}
                                          onChange={e => updateItemField(section.id, itemIndex, field.key, e.target.value)}
                                        />
                                      </label>
                                    ))}
                                  </div>
                                  {listKey && (
                                    <div className="editor-bullets">
                                      {((item[listKey] as string[]) || []).map((value, valueIndex) => (
                                        <div key={valueIndex} className="editor-bullet-row">
                                          <input value={value} onChange={e => updateListValue(section.id, itemIndex, listKey, valueIndex, e.target.value)} />
                                          <button
                                            type="button" className="editor-icon-btn"
                                            onClick={() => removeListValue(section.id, itemIndex, listKey, valueIndex)} title="Remove"
                                          ><Trash2 size={13} /></button>
                                        </div>
                                      ))}
                                      <button type="button" className="editor-add-link" onClick={() => addListValue(section.id, itemIndex, listKey)}>
                                        <Plus size={12} /> Add {LIST_LABEL[section.type]}
                                      </button>
                                    </div>
                                  )}
                                </div>
                              )
                            })}
                            <button type="button" className="editor-add-link" onClick={() => addItem(section.id)} style={{ marginTop: 10 }}>
                              <Plus size={12} /> Add {SECTION_LABELS[section.type].toLowerCase()} item
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <select
                className="job-picker" style={{ marginTop: 12 }} defaultValue=""
                onChange={e => { if (e.target.value) { addSection(e.target.value as ResumeSectionType); e.target.value = '' } }}
              >
                <option value="" disabled>+ Add a section…</option>
                {(Object.keys(SECTION_LABELS) as ResumeSectionType[]).map(t => (
                  <option key={t} value={t}>{SECTION_LABELS[t]}</option>
                ))}
              </select>

              <div className="editor-section-card" style={{ marginTop: 12 }}>
                <div className="editor-latex-toggle" onClick={() => setShowLatex(v => !v)}>
                  <span>LaTeX source</span>
                  {showLatex ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
                </div>
                {showLatex && (
                  <div className="editor-latex-source">
                    <div className="card-actions"><CopyButton text={latex} /></div>
                    <pre>{latex}</pre>
                  </div>
                )}
              </div>
            </div>

            <div className="editor-preview">
              <div className="editor-preview-head">
                <span className={`editor-status-pill${compiling ? ' compiling' : compileError ? ' error' : ''}`}>
                  {compiling ? <RefreshCw size={12} className="spin" /> : <Check size={12} />}
                  {compiling ? 'Compiling…' : compileError ? 'Compile error' : 'PDF up to date'}
                </span>
                <div className="editor-preview-actions">
                  <button type="button" className="outline-button" onClick={() => doc && compileNow(doc)} disabled={compiling}>
                    <RefreshCw size={13} className={compiling ? 'spin' : ''} /> Compile now
                  </button>
                  {pdfUrl && (
                    <button type="button" className="primary-button" onClick={downloadPdf}>
                      <Download size={13} /> Download PDF
                    </button>
                  )}
                </div>
              </div>
              {compileError && <p className="form-error" style={{ marginBottom: 10 }}>{compileError}</p>}
              {pdfUrl ? (
                <iframe className="editor-pdf-frame" src={pdfUrl} title="Resume preview" />
              ) : (
                <div className="editor-empty-pdf">{compiling ? 'Compiling your first preview…' : 'Your compiled resume will appear here.'}</div>
              )}
            </div>
          </div>
        )}
      </AppShell>
    </RequireAuth>
  )
}
