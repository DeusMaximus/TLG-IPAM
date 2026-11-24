import { useRef, useState } from 'react'
import { Check, Copy } from 'lucide-react'

// Copy with the Clipboard API where the page is a secure context; on plain http
// (common on a LAN) fall back to selecting the text and execCommand('copy').
async function copyText(text, element) {
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to the selection fallback */
  }
  if (!element) return false
  const range = document.createRange()
  range.selectNodeContents(element)
  const selection = window.getSelection()
  selection.removeAllRanges()
  selection.addRange(range)
  try {
    return document.execCommand('copy')
  } catch {
    return false
  }
}

export default function CopyBlock({ label, text, testId }) {
  const ref = useRef(null)
  const [state, setState] = useState(null)

  const copy = async () => {
    setState((await copyText(text, ref.current)) ? 'copied' : 'failed')
  }

  return (
    <div className="mt-3">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-xs font-medium text-slate-400">{label}</span>
        <button
          type="button"
          onClick={copy}
          className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200"
          aria-label={`Copy ${label.toLowerCase()}`}
        >
          {state === 'copied' ? <Check size={12} /> : <Copy size={12} />}
          {state === 'copied' ? 'Copied' : state === 'failed' ? 'Select and copy' : 'Copy'}
        </button>
      </div>
      <pre
        ref={ref}
        data-testid={testId}
        className="select-all overflow-x-auto whitespace-pre rounded-md border border-slate-700 bg-slate-900 px-3 py-2 font-mono text-xs text-slate-200"
      >
        {text}
      </pre>
    </div>
  )
}
