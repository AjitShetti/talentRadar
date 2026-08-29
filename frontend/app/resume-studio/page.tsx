'use client'

import Link from 'next/link'
import { ChangeEvent, useEffect, useRef, useState } from 'react'
import { Check, Download, FileText, RefreshCw, Sparkles, UploadCloud, WandSparkles } from 'lucide-react'
import AppShell from '@/components/AppShell'
import RequireAuth from '@/components/RequireAuth'
import CopyButton from '@/components/CopyButton'
import { api, AtsResult, TailorResult, TargetJob } from '@/lib/api'
import { usePersistentState } from '@/lib/persistent-state'

function base64ToBlob(base64: string, type: string) {
  const binary = atob(base64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  return new Blob([bytes], { type })
}

function formatDate(iso: string | null) {
  if (!iso) return ''
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  } catch {
    return ''
  }
}

function errorMessage(err: unknown, fallback: string) {
  return err instanceof Error ? err.message : fallback
}

export default function ResumeStudioPage() {
  const [resumeText, setResumeText] = useState('')
  const [fileName, setFileName] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<string | null>(null)
  const [resumeLoading, setResumeLoading] = useState(true)
  const [extracting, setExtracting] = useState(false)
  const [uploadError, setUploadError] = useState('')

  const [targetJobs, setTargetJobs] = useState<TargetJob[]>([])
  const [selectedJobId, setSelectedJobId] = usePersistentState('resumeStudio.selectedJobId', '')
  const [jobDescription, setJobDescription] = usePersistentState('resumeStudio.jobDescription', '')
  const [jobTitle, setJobTitle] = usePersistentState('resumeStudio.jobTitle', '')
  const [company, setCompany] = usePersistentState('resumeStudio.company', '')

  const [ats, setAts] = usePersistentState<AtsResult | null>('resumeStudio.ats', null)
  const [atsLoading, setAtsLoading] = useState(false)
  const [atsError, setAtsError] = useState('')

  const [tailor, setTailor] = usePersistentState<TailorResult | null>('resumeStudio.tailor', null)
  const [tailorLoading, setTailorLoading] = useState(false)
  const [tailorError, setTailorError] = useState('')

  const [cover, setCover] = usePersistentState('resumeStudio.cover', '')
  const [coverLoading, setCoverLoading] = useState(false)
  const [coverError, setCoverError] = useState('')

  const [prepareError, setPrepareError] = useState('')

  // Read inside the async loader below without making it depend on the value.
  const selectedJobIdRef = useRef(selectedJobId)
  selectedJobIdRef.current = selectedJobId

  useEffect(() => {
    api.resumes.me()
      .then(saved => {
        if (saved) {
          setResumeText(saved.extracted_text)
          setFileName(saved.filename)
          setSavedAt(saved.updated_at)
        }
      })
      .catch(() => {})
      .finally(() => setResumeLoading(false))

    api.resumes.targetJobs()
      .then(jobs => {
        setTargetJobs(jobs)
        const restored = selectedJobIdRef.current
        if (restored && !jobs.some(job => job.id === restored)) setSelectedJobId('')
        else if (jobs.length > 0 && !restored) applyJob(jobs[0])
      })
      .catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function applyJob(job: TargetJob) {
    setSelectedJobId(job.id)
    setJobDescription(job.description || '')
    setJobTitle(job.title)
    setCompany(job.company_name || '')
  }

  function handleJobSelect(e: ChangeEvent<HTMLSelectElement>) {
    const id = e.target.value
    setSelectedJobId(id)
    const job = targetJobs.find(j => j.id === id)
    if (job) applyJob(job)
  }

  async function handleFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    setUploadError('')
    setExtracting(true)
    try {
      const saved = await api.resumes.extractText(file)
      setResumeText(saved.extracted_text)
      setFileName(saved.filename)
      setSavedAt(saved.updated_at)
    } catch (err) {
      setUploadError(errorMessage(err, 'Could not read that file.'))
    } finally {
      setExtracting(false)
    }
  }

  async function runAnalyze() {
    setAtsLoading(true)
    setAtsError('')
    try {
      setAts(await api.resumes.analyze({ resume_text: resumeText, job_description: jobDescription, job_title: jobTitle || undefined }))
    } catch (err) {
      setAtsError(errorMessage(err, 'ATS analysis failed.'))
    } finally {
      setAtsLoading(false)
    }
  }

  async function runTailor() {
    setTailorLoading(true)
    setTailorError('')
    try {
      setTailor(await api.resumes.tailor({ resume_text: resumeText, job_description: jobDescription, job_title: jobTitle || undefined }))
    } catch (err) {
      setTailorError(errorMessage(err, 'Resume tailoring failed.'))
    } finally {
      setTailorLoading(false)
    }
  }

  async function runCoverLetter() {
    if (!jobTitle.trim() || !company.trim()) {
      setCoverError('Add a job title and company above to generate a cover letter.')
      return
    }
    setCoverLoading(true)
    setCoverError('')
    try {
      const response = await api.resumes.coverLetter({ resume_text: resumeText, job_description: jobDescription, job_title: jobTitle, company, tone: 'professional' })
      setCover(response.content)
    } catch (err) {
      setCoverError(errorMessage(err, 'Cover letter generation failed.'))
    } finally {
      setCoverLoading(false)
    }
  }

  async function runPrepare() {
    if (!resumeText.trim() || !jobDescription.trim()) {
      setPrepareError('Add your resume and pick a tracked job first.')
      return
    }
    setPrepareError('')
    await Promise.allSettled([runAnalyze(), runTailor(), runCoverLetter()])
  }

  function downloadPdf() {
    if (!tailor?.pdf_base64) return
    const url = URL.createObjectURL(base64ToBlob(tailor.pdf_base64, 'application/pdf'))
    const a = document.createElement('a')
    a.href = url
    a.download = tailor.filename || 'tailored_resume.pdf'
    a.click()
    URL.revokeObjectURL(url)
  }

  const anyLoading = atsLoading || tailorLoading || coverLoading
  const readyToPrepare = resumeText.trim().length > 0 && jobDescription.trim().length > 0

  return (
    <RequireAuth>
      <AppShell>
        <section className="page-heading">
          <p className="eyebrow">RESUME STUDIO</p>
          <h1>Turn your experience into a stronger yes.</h1>
          <p>Your resume is remembered — pick a job you're tracking and one click checks your ATS score, tailors your resume, and writes a cover letter.</p>
          <Link href="/resume-studio/editor" className="outline-button" style={{ display: 'inline-flex', marginTop: 14 }}>
            <FileText size={13} /> Open the section-by-section LaTeX editor
          </Link>
        </section>

        <div className="studio-grid">
          <div className="resume-form">
            <div className="setup-section">
              <label>Your resume</label>
              {fileName && !resumeLoading && (
                <p className="resume-status">
                  <Check size={13} /> Using <strong>{fileName}</strong>{savedAt ? ` · saved ${formatDate(savedAt)}` : ''}
                </p>
              )}
              <div className="resume-upload">
                <label htmlFor="resume-file" className="outline-button upload-trigger">
                  <UploadCloud size={14} />
                  {extracting ? 'Reading file…' : fileName ? 'Replace resume' : 'Upload PDF or DOCX'}
                </label>
                <input id="resume-file" type="file" accept=".pdf,.docx,.txt" hidden onChange={handleFile} />
              </div>
              {uploadError && <p className="form-error">{uploadError}</p>}
              <textarea
                className="resume-text-area"
                value={resumeText}
                onChange={e => setResumeText(e.target.value)}
                placeholder="Upload a file above, or paste your resume text here…"
              />
            </div>

            <div className="setup-section">
              <label>Target job</label>
              {targetJobs.length > 0 ? (
                <select className="job-picker" value={selectedJobId} onChange={handleJobSelect}>
                  <option value="">— Choose a job you're tracking —</option>
                  {targetJobs.map(job => (
                    <option key={job.id} value={job.id}>
                      {job.title} · {job.company_name || 'Unknown company'}
                    </option>
                  ))}
                </select>
              ) : (
                <p className="muted-copy">
                  No tracked jobs yet. <Link href="/search">Find a role</Link> and save it to your tracker — its description is what the studio tailors against.
                </p>
              )}
              {selectedJobId && !jobDescription.trim() && (
                <p className="form-error">This posting was saved without a description, so there is nothing to tailor against. Pick another job.</p>
              )}
              <div className="inline-fields">
                <label>
                  Job title
                  <input value={jobTitle} onChange={e => setJobTitle(e.target.value)} placeholder="e.g. Product Designer" />
                </label>
                <label>
                  Company
                  <input value={company} onChange={e => setCompany(e.target.value)} placeholder="e.g. Miro" />
                </label>
              </div>
            </div>

            {prepareError && <p className="form-error">{prepareError}</p>}
            <button type="button" className="primary-button full-width prepare-button" disabled={anyLoading || !readyToPrepare} onClick={runPrepare}>
              <Sparkles size={15} />
              {anyLoading ? 'Preparing your application…' : 'Prepare my application'}
            </button>
            <p className="muted-copy prepare-hint">Checks your ATS score, tailors your resume, and writes a cover letter in one go. Use the refresh icon on any card to redo just that piece.</p>
          </div>

          <div className="studio-results">
            <div className="studio-card">
              <div className="studio-card-head">
                <h2>
                  <FileText size={15} /> ATS score
                </h2>
                <button type="button" className="icon-refresh" title="Redo ATS check" disabled={atsLoading || !readyToPrepare} onClick={runAnalyze}>
                  <RefreshCw size={13} className={atsLoading ? 'spin' : ''} />
                </button>
              </div>
              {atsError && <p className="form-error">{atsError}</p>}
              {!ats && !atsError && !atsLoading && <div className="card-empty">Your ATS score will appear here once you click "Prepare my application".</div>}
              {atsLoading && <div className="card-empty">Analyzing…</div>}
              {ats && (
                <>
                  <div className="ats-score-row">
                    <span className="ats-score-badge">
                      {Math.round(ats.ats_score)}
                      <small>/100</small>
                    </span>
                    {ats.reasoning && <p className="muted-copy">{ats.reasoning}</p>}
                  </div>
                  {ats.matched_skills.length > 0 && (
                    <div className="skill-section">
                      <h3>Matched skills</h3>
                      <div className="chips">
                        {ats.matched_skills.map(skill => (
                          <span key={skill} className="chip-good">{skill}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {ats.missing_skills.length > 0 && (
                    <div className="skill-section">
                      <h3>Missing skills</h3>
                      <div className="chips">
                        {ats.missing_skills.map(skill => (
                          <span key={skill} className="chip-bad">{skill}</span>
                        ))}
                      </div>
                    </div>
                  )}
                  {ats.suggestions.length > 0 && (
                    <div className="skill-section">
                      <h3>Suggestions</h3>
                      <ul className="suggestion-list">
                        {ats.suggestions.map(item => (
                          <li key={item}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </>
              )}
            </div>

            <div className="studio-card">
              <div className="studio-card-head">
                <h2>
                  <WandSparkles size={15} /> Tailored resume
                </h2>
                <button type="button" className="icon-refresh" title="Redo tailoring" disabled={tailorLoading || !readyToPrepare} onClick={runTailor}>
                  <RefreshCw size={13} className={tailorLoading ? 'spin' : ''} />
                </button>
              </div>
              {tailorError && <p className="form-error">{tailorError}</p>}
              {!tailor && !tailorError && !tailorLoading && <div className="card-empty">Your rewritten resume will appear here once you click "Prepare my application".</div>}
              {tailorLoading && <div className="card-empty">Tailoring your resume…</div>}
              {tailor && (
                <>
                  <div className="card-actions">
                    {tailor.pdf_base64 && (
                      <button type="button" className="outline-button" onClick={downloadPdf}>
                        <Download size={13} /> Download PDF
                      </button>
                    )}
                    <CopyButton text={tailor.latex_content} />
                  </div>
                  {!tailor.pdf_base64 && <p className="form-error">PDF rendering was unavailable — copy the LaTeX below to compile it yourself.</p>}
                  <pre className="generated-copy">{tailor.latex_content}</pre>
                </>
              )}
            </div>

            <div className="studio-card">
              <div className="studio-card-head">
                <h2>
                  <Sparkles size={15} /> Cover letter
                </h2>
                <button type="button" className="icon-refresh" title="Redo cover letter" disabled={coverLoading || !readyToPrepare} onClick={runCoverLetter}>
                  <RefreshCw size={13} className={coverLoading ? 'spin' : ''} />
                </button>
              </div>
              {coverError && <p className="form-error">{coverError}</p>}
              {!cover && !coverError && !coverLoading && <div className="card-empty">Your cover letter will appear here once you click "Prepare my application".</div>}
              {coverLoading && <div className="card-empty">Writing your cover letter…</div>}
              {cover && (
                <>
                  <div className="card-actions">
                    <CopyButton text={cover} />
                  </div>
                  <pre className="generated-copy">{cover}</pre>
                </>
              )}
            </div>
          </div>
        </div>
      </AppShell>
    </RequireAuth>
  )
}
