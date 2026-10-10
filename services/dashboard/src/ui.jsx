import React, { useEffect, useId, useRef, useState } from 'react'

const STATUS = {
  connected: 'Connected',
  unreachable: 'Unreachable',
  disabled: 'Disabled',
  unknown: 'Checking',
}

export function StatusPill({ status }) {
  const key = STATUS[status] ? status : 'unknown'
  return (
    <span className={`pill pill-${key}`}>
      <span className="lamp" aria-hidden="true" />
      {STATUS[key]}
    </span>
  )
}

export function CopyButton({ value, label = 'Copy' }) {
  const [copied, setCopied] = useState(false)
  async function copy() {
    try {
      await navigator.clipboard.writeText(value)
    } catch {
      const area = document.createElement('textarea')
      area.value = value
      document.body.appendChild(area)
      area.select()
      document.execCommand('copy')
      area.remove()
    }
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }
  return (
    <button type="button" className="btn btn-quiet" onClick={copy} aria-live="polite">
      {copied ? 'Copied' : label}
    </button>
  )
}

export function Secret({ label, value }) {
  return (
    <div className="secret">
      <div className="secret-head">
        <span>{label}</span>
        <CopyButton value={value} />
      </div>
      <code className="secret-value">{value}</code>
    </div>
  )
}

export function Modal({ title, onClose, children, wide = false }) {
  const panel = useRef(null)
  useEffect(() => {
    const previous = document.activeElement
    const first = panel.current?.querySelector('input, select, textarea, button:not(.modal-close)')
    first?.focus()
    const onKey = (e) => e.key === 'Escape' && onClose()
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('keydown', onKey)
      previous?.focus?.()
    }
  }, [onClose])
  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={`modal${wide ? ' modal-wide' : ''}`} role="dialog" aria-modal="true" aria-label={title} ref={panel}>
        <div className="modal-head">
          <h2>{title}</h2>
          <button type="button" className="btn btn-quiet modal-close" onClick={onClose} aria-label="Close">
            Close
          </button>
        </div>
        {children}
      </div>
    </div>
  )
}

/** Shown once after something secret is created. The values are not kept anywhere else. */
export function SecretsModal({ title, intro, items, onClose }) {
  return (
    <Modal title={title} onClose={onClose}>
      <p className="note">{intro}</p>
      {items.map((item) => (
        <Secret key={item.label} label={item.label} value={item.value} />
      ))}
      <div className="actions">
        <button type="button" className="btn btn-primary" onClick={onClose}>
          I have copied {items.length === 1 ? 'it' : 'them'}
        </button>
      </div>
    </Modal>
  )
}

export function Field({ label, hint, children }) {
  // The hint is linked as a description, so it is not read as part of the field's name.
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const control = React.cloneElement(React.Children.only(children), { id, 'aria-describedby': hintId })
  return (
    <div className="field">
      <label htmlFor={id} className="field-label">
        {label}
      </label>
      {control}
      {hint && (
        <span id={hintId} className="field-hint">
          {hint}
        </span>
      )}
    </div>
  )
}

export function ErrorNote({ children }) {
  return children ? (
    <p className="error" role="alert">
      {children}
    </p>
  ) : null
}

export function Confirm({ title, children, confirmLabel, cancelLabel, onConfirm, onClose, busy, error }) {
  return (
    <Modal title={title} onClose={onClose}>
      {children}
      <ErrorNote>{error}</ErrorNote>
      <div className="actions">
        <button type="button" className="btn btn-secondary" onClick={onClose}>
          {cancelLabel}
        </button>
        <button type="button" className="btn btn-danger" onClick={onConfirm} disabled={busy}>
          {confirmLabel}
        </button>
      </div>
    </Modal>
  )
}

export function PageHead({ title, children }) {
  return (
    <header className="page-head">
      <h1>{title}</h1>
      {children}
    </header>
  )
}
