import { useState, type FormEvent } from 'react'
import {
  ArrowRight, BookOpen, Check, ChevronRight, CircleHelp,
  Code2, Compass, ExternalLink, FileCode2, Folder, Github,
  GitFork, GitPullRequest, LoaderCircle, Package, Play,
  Search, ShieldCheck, Sparkles, Star, Terminal, Wrench, Zap
} from 'lucide-react'
import { postAnalyze } from './api.js'

type Issue = { number: number; title: string; url: string; labels: string[]; score: number; reasons: string[]; body: string }
type Result = {
  repository: {
    full_name: string; url: string; default_branch: string;
    description: string | null; language: string | null;
    stars: number; forks: number; license: string | null
  }
  guide: {
    project_summary: string; technology_stack: string[]; prerequisites: string[];
    setup_steps: string[]; run_instructions: string[]; build_commands: string[];
    test_commands: string[]; important_directories: string[];
    entry_points: { path: string; basis: string }[];
    where_to_start: string; recommended_issues: Issue[];
    first_contribution: { suggestion: string; issue_url: string | null };
    contribution_workflow: string[]; warnings: string[];
    claim_evidence?: { claim: string; evidence_ids: string[] }[]
  }
  evidence: { id: string; label: string; url: string; excerpt: string }[]
  analysis: {
    dependencies: string[]; files_inspected: string[];
    tree: string; profile: Record<string, unknown>;
    fingerprint: Record<string, unknown>; context_size: number
  }
  metadata: { commit_sha: string | null; model: string; generated_at: string }
}

const features = [
  'Understand any project in plain English',
  'See exactly what tools you need installed',
  'Find the most important files to read first',
  'Discover beginner-friendly issues to tackle',
  'Get a clear step-by-step setup guide',
  'Know how to make your first contribution',
]

function StepBadge({ n }: { n: number }) {
  return <span className="step-badge">0{n}</span>
}

function SectionTag({ children }: { children: React.ReactNode }) {
  return <span className="section-tag">{children}</span>
}

function BlockCard({ icon, title, tag, children, accent }: {
  icon: React.ReactNode; title: string; tag?: string;
  children: React.ReactNode; accent?: boolean
}) {
  return (
    <div className={`block-card${accent ? ' block-card--accent' : ''}`}>
      <div className="block-card-header">
        <span className="block-card-icon">{icon}</span>
        <div>
          {tag && <SectionTag>{tag}</SectionTag>}
          <h3 className="block-card-title">{title}</h3>
        </div>
      </div>
      <div className="block-card-body">{children}</div>
    </div>
  )
}

function CommandPill({ children }: { children: string }) {
  return <code className="cmd-pill">{children}</code>
}

function NumberedStep({ index, children }: { index: number; children: React.ReactNode }) {
  return (
    <div className="numbered-step">
      <span className="numbered-step-num">{index + 1}</span>
      <span className="numbered-step-text">{children}</span>
    </div>
  )
}

function IssueCard({ issue }: { issue: Issue }) {
  const difficulty = issue.labels.find(l =>
    /beginner|good.first|easy|starter|help.wanted/i.test(l)
  )
  return (
    <a className="issue-card" href={issue.url} target="_blank" rel="noreferrer">
      <div className="issue-card-top">
        <span className="issue-card-num">#{issue.number}</span>
        {difficulty && <span className="issue-card-badge">{difficulty}</span>}
      </div>
      <p className="issue-card-title">{issue.title}</p>
      {issue.labels.length > 0 && (
        <div className="issue-card-labels">
          {issue.labels.slice(0, 3).map(l => (
            <span key={l} className="issue-label">{l}</span>
          ))}
        </div>
      )}
      <span className="issue-card-cta">View issue <ChevronRight size={12} /></span>
    </a>
  )
}

export default function App() {
  const [url, setUrl] = useState('')
  const [result, setResult] = useState<Result | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function analyze(event: FormEvent) {
    event.preventDefault()
    setError('')
    setResult(null)
    if (!/^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/?$/.test(url.trim())) {
      setError('Please enter a valid GitHub link, like https://github.com/facebook/react')
      return
    }
    setLoading(true)
    try {
      const body = await postAnalyze(url.trim()) as Result
      setResult(body)
      setTimeout(() => document.getElementById('results')?.scrollIntoView({ behavior: 'smooth' }), 80)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not analyze repository. Please try again.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main>
      {/* ── NAV ── */}
      <nav className="nav wrap">
        <a className="brand" href="#top">
          <span className="brand-icon"><Compass size={18} /></span>
          contrib<span>compass</span>
        </a>
        <div className="nav-right">
          <span className="open-badge"><i />OPEN SOURCE</span>
          <a className="github-link" href="https://github.com" target="_blank" rel="noreferrer">
            <Github size={15} /> GitHub <ExternalLink size={11} />
          </a>
        </div>
      </nav>

      {/* ── HERO ── */}
      <section id="top" className="hero wrap">
        <div className="hero-copy">
          <div className="eyebrow"><Sparkles size={13} /> BEGINNER FRIENDLY · EVIDENCE BACKED</div>
          <h1>Jump into<br /><em>any open-source</em><br />project.</h1>
          <p className="hero-desc">
            Paste a GitHub link and get a plain-English guide — what the project does,
            how to set it up, and exactly where you can help.
          </p>
          <div className="trust-line"><ShieldCheck size={15} /> Based on the repo's actual files, not guesswork.</div>
        </div>

        <div className="analyze-card">
          <div className="card-top">
            <div>
              <span className="card-label">GET STARTED</span>
              <h2>Paste a GitHub link</h2>
            </div>
            <span className="card-step">Takes under a minute</span>
          </div>
          <form onSubmit={analyze}>
            <label htmlFor="repo-url">GITHUB REPOSITORY URL</label>
            <div className="input-wrap">
              <Github size={16} />
              <input
                id="repo-url" type="url" autoComplete="url"
                placeholder="https://github.com/owner/repository"
                value={url} onChange={e => setUrl(e.target.value)}
              />
            </div>
            <button disabled={loading} type="submit">
              {loading
                ? <><LoaderCircle size={16} className="spin" /> Analyzing repository…</>
                : <>Analyze this repository <ArrowRight size={16} /></>}
            </button>
            {error && <p className="error" role="alert">{error}</p>}
          </form>
          <div className="card-foot">
            <span><i />Works with any public repository</span>
            <span>Free to use</span>
          </div>
        </div>

        <div className="hero-orbit orbit-one" />
        <div className="hero-orbit orbit-two" />
      </section>

      {/* ── FEATURES ── */}
      <section className="features wrap">
        <div className="section-kicker">WHAT YOU GET</div>
        <h2>Everything a beginner needs. <span>Nothing confusing.</span></h2>
        <div className="feature-grid">
          {features.map((f, i) => (
            <div className="feature" key={f}>
              <span className="feature-num">0{i + 1}</span>
              <span>{f}</span>
              <Check size={14} />
            </div>
          ))}
        </div>
      </section>

      {/* ── HOW IT WORKS ── */}
      <section className="steps">
        <div className="wrap steps-inner">
          <div>
            <span className="section-kicker">HOW IT WORKS</span>
            <h2>Three steps<br />to clarity.</h2>
          </div>
          <div className="step-list">
            <div>
              <StepBadge n={1} />
              <div>
                <h3>We read the repository</h3>
                <p>We scan the source code, README, setup files, and open issues directly from GitHub.</p>
              </div>
              <Search size={17} />
            </div>
            <div>
              <StepBadge n={2} />
              <div>
                <h3>We build your guide</h3>
                <p>A plain-English breakdown is generated from what we actually found — no hallucinations.</p>
              </div>
              <BookOpen size={17} />
            </div>
            <div>
              <StepBadge n={3} />
              <div>
                <h3>You make your move</h3>
                <p>Pick an issue, follow the setup steps, and open your first pull request with confidence.</p>
              </div>
              <GitPullRequest size={17} />
            </div>
          </div>
        </div>
      </section>

      {/* ── RESULTS ── */}
      {result && (
        <section id="results" className="results wrap">

          {/* Header */}
          <div className="result-heading">
            <div>
              <SectionTag>ANALYSIS COMPLETE</SectionTag>
              <h2>{result.repository.full_name}</h2>
              <p className="result-desc">{result.repository.description || 'No description provided.'}</p>
            </div>
            <a className="outline-link" href={result.repository.url} target="_blank" rel="noreferrer">
              Open on GitHub <ExternalLink size={13} />
            </a>
          </div>

          {/* Stats bar */}
          <div className="stats-bar">
            <div className="stat-item">
              <Code2 size={14} />
              <span>{result.repository.language || 'Language unknown'}</span>
              <span className="stat-label">Language</span>
            </div>
            <div className="stat-item">
              <Star size={14} />
              <span>{result.repository.stars.toLocaleString()}</span>
              <span className="stat-label">Stars</span>
            </div>
            <div className="stat-item">
              <GitFork size={14} />
              <span>{result.repository.forks.toLocaleString()}</span>
              <span className="stat-label">Forks</span>
            </div>
            <div className="stat-item">
              <ShieldCheck size={14} />
              <span>{result.repository.license || 'No license'}</span>
              <span className="stat-label">License</span>
            </div>
          </div>

          {/* Warning banner */}
          {!!result.guide.warnings.length && (
            <div className="warning-banner">
              <CircleHelp size={16} />
              <div>
                <b>A note on this analysis</b>
                <ul>{result.guide.warnings.map(x => <li key={x}>{x}</li>)}</ul>
              </div>
            </div>
          )}

          {/* ── BLOCK 1: What this project does ── */}
          <BlockCard
            icon={<BookOpen size={20} />}
            tag="WHAT IS THIS PROJECT?"
            title="Project overview"
          >
            <p className="block-prose">{result.guide.project_summary}</p>
            {result.guide.technology_stack.length > 0 && (
              <>
                <h4 className="block-label"><Package size={13} /> Built with</h4>
                <div className="chip-row">
                  {result.guide.technology_stack.map(x => <span className="chip" key={x}>{x}</span>)}
                </div>
              </>
            )}
          </BlockCard>

          {/* ── BLOCK 2: Repository Architecture ── */}
          {(result.guide.entry_points.length > 0 || result.guide.important_directories.length > 0) && (
            <BlockCard
              icon={<Folder size={20} />}
              tag="REPOSITORY ARCHITECTURE"
              title="How the code is organized"
            >
              <p className="block-hint">These are the most important places to look when you first open the project.</p>
              {result.guide.important_directories.length > 0 && (
                <>
                  <h4 className="block-label"><Folder size={13} /> Key folders</h4>
                  <div className="dir-list">
                    {result.guide.important_directories.map(d => (
                      <div className="dir-item" key={d}>
                        <FileCode2 size={13} />
                        <code>{d}</code>
                      </div>
                    ))}
                  </div>
                </>
              )}
              {result.guide.entry_points.length > 0 && (
                <>
                  <h4 className="block-label"><Zap size={13} /> Entry points — start reading here</h4>
                  <div className="entry-list">
                    {result.guide.entry_points.map(ep => (
                      <a
                        key={ep.path}
                        className="entry-item"
                        href={`${result.repository.url}/blob/${encodeURIComponent(result.repository.default_branch)}/${ep.path.split('/').map(encodeURIComponent).join('/')}`}
                        target="_blank" rel="noreferrer"
                      >
                        <code>{ep.path}</code>
                        <span>{ep.basis}</span>
                        <ExternalLink size={11} />
                      </a>
                    ))}
                  </div>
                </>
              )}
            </BlockCard>
          )}

          {/* ── BLOCK 3: Setup guide ── */}
          <BlockCard
            icon={<Terminal size={20} />}
            tag="HOW TO SET IT UP"
            title="Getting started on your machine"
          >
            {result.guide.prerequisites.length > 0 && (
              <>
                <h4 className="block-label"><Package size={13} /> What you need installed first</h4>
                <div className="prereq-list">
                  {result.guide.prerequisites.map((x, i) => (
                    <div className="prereq-item" key={i}>
                      <Check size={13} className="prereq-check" />
                      <span>{x}</span>
                    </div>
                  ))}
                </div>
              </>
            )}
            {result.guide.setup_steps.length > 0 && (
              <>
                <h4 className="block-label"><Play size={13} /> Setup steps</h4>
                <div className="step-flow">
                  {result.guide.setup_steps.map((x, i) => (
                    <NumberedStep key={i} index={i}>{x}</NumberedStep>
                  ))}
                </div>
              </>
            )}
            {result.guide.run_instructions.length > 0 && (
              <>
                <h4 className="block-label"><Play size={13} /> How to run it locally</h4>
                <div className="cmd-list">
                  {result.guide.run_instructions.map((x, i) => <CommandPill key={i}>{x}</CommandPill>)}
                </div>
              </>
            )}
            {(result.guide.build_commands.length > 0 || result.guide.test_commands.length > 0) && (
              <>
                <h4 className="block-label"><Wrench size={13} /> Build &amp; test commands</h4>
                <div className="cmd-list">
                  {[...result.guide.build_commands, ...result.guide.test_commands].map((x, i) => (
                    <CommandPill key={i}>{x}</CommandPill>
                  ))}
                </div>
              </>
            )}
          </BlockCard>

          {/* ── BLOCK 4 + 5: Issues & First Contribution side-by-side ── */}
          <div className="two-col-row">

            {/* Issues */}
            <BlockCard
              icon={<Search size={20} />}
              tag="OPEN ISSUES"
              title="Good places to start"
            >
              {result.guide.recommended_issues.length > 0 ? (
                <div className="issues-list">
                  {result.guide.recommended_issues.map(issue => (
                    <IssueCard key={issue.number} issue={issue} />
                  ))}
                </div>
              ) : (
                <p className="block-hint">
                  No open issues were returned for this repository right now.
                  Browse the repo directly — docs, typos, and test coverage are always great first contributions.
                </p>
              )}
            </BlockCard>

            {/* First Contribution */}
            <BlockCard
              icon={<GitPullRequest size={20} />}
              tag="YOUR FIRST CONTRIBUTION"
              title="Here's a concrete first step"
              accent
            >
              <p className="block-prose">{result.guide.first_contribution.suggestion}</p>
              {result.guide.first_contribution.issue_url && (
                <a className="cta-link" href={result.guide.first_contribution.issue_url} target="_blank" rel="noreferrer">
                  View the suggested issue <ArrowRight size={14} />
                </a>
              )}
              {result.guide.where_to_start && (
                <>
                  <h4 className="block-label" style={{ marginTop: '18px' }}><FileCode2 size={13} /> Where to look in the code</h4>
                  <p className="block-hint">{result.guide.where_to_start}</p>
                </>
              )}
            </BlockCard>
          </div>

          {/* ── BLOCK 6: How to contribute ── */}
          {result.guide.contribution_workflow.length > 0 && (
            <BlockCard
              icon={<GitFork size={20} />}
              tag="HOW TO CONTRIBUTE"
              title="The contribution process"
            >
              <p className="block-hint">Most projects follow a similar workflow. Here's what this one expects:</p>
              <div className="step-flow">
                {result.guide.contribution_workflow.map((x, i) => (
                  <NumberedStep key={i} index={i}>{x}</NumberedStep>
                ))}
              </div>
            </BlockCard>
          )}

          {/* ── BLOCK 7: Source evidence ── */}
          <BlockCard
            icon={<ShieldCheck size={20} />}
            tag="SOURCE MATERIAL"
            title="What we based this on"
          >
            <p className="block-hint">
              Every recommendation above comes from files we actually read in this repository.
              Click any file below to verify.
            </p>
            <div className="evidence-grid">
              {result.evidence.map(item => (
                <a id={item.id} key={item.id} href={item.url} target="_blank" rel="noreferrer" className="evidence-item">
                  <span className="evidence-id">{item.id}</span>
                  <b className="evidence-label">{item.label}</b>
                  {item.excerpt && <p className="evidence-excerpt">{item.excerpt}</p>}
                  <span className="evidence-view">View file <ExternalLink size={10} /></span>
                </a>
              ))}
            </div>
          </BlockCard>

          <div className="analysis-meta">
            {result.analysis.files_inspected.length} files inspected ·{' '}
            <span title={result.metadata.commit_sha || 'Unknown'}>
              commit {result.metadata.commit_sha?.slice(0, 7) || 'unknown'}
            </span>
            {' '}· Analysis by GitCompass
          </div>
        </section>
      )}

      {/* ── FOOTER ── */}
      <footer className="footer wrap">
        <a className="brand" href="#top">
          <span className="brand-icon"><Compass size={16} /></span>
          contrib<span>compass</span>
        </a>
        <p>Your guide into open source.</p>
        <span>Evidence first. Always.</span>
      </footer>
    </main>
  )
}
