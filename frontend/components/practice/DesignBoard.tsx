'use client'

import { Component, ReactNode, useEffect, useRef, useState } from 'react'
import { Excalidraw, MainMenu } from '@excalidraw/excalidraw'
import '@excalidraw/excalidraw/index.css'

export type DesignBoardProps = {
  elements: unknown[] | null
  readOnly: boolean
  onChange: (elements: unknown[]) => void
  onLoadFailed: () => void
}

type Scene = NonNullable<NonNullable<React.ComponentProps<typeof Excalidraw>['initialData']>>
type Theme = 'light' | 'dark'

const UI_OPTIONS: React.ComponentProps<typeof Excalidraw>['UIOptions'] = {
  canvasActions: {
    changeViewBackgroundColor: false,
    export: false,
    loadScene: false,
    saveAsImage: false,
    saveToActiveFile: false,
    toggleTheme: false,
  },
  tools: { image: false },
}

const pageTheme = (): Theme => document.documentElement.dataset.theme === 'light' ? 'light' : 'dark'

/** Every edit raises an element's version, a deletion included, and nothing else does. */
const versionOf = (elements: readonly { version: number }[]) => elements.reduce((sum, element) => sum + element.version, 0)

/** A crash inside the whiteboard must leave the problem and the reasoning on the page. */
class Guard extends Component<{ onFailed: () => void; children: ReactNode }, { failed: boolean }> {
  state = { failed: false }
  static getDerivedStateFromError() { return { failed: true } }
  componentDidCatch() { this.props.onFailed() }
  render() { return this.state.failed ? null : this.props.children }
}

/** Excalidraw, with everything that saves elsewhere, opens another site or holds an image taken away. */
export default function DesignBoard({ elements, readOnly, onChange, onLoadFailed }: DesignBoardProps) {
  const [theme, setTheme] = useState<Theme>(pageTheme)
  const [scene] = useState(() => ({ elements: elements ?? [], scrollToContent: true }) as Scene)
  // It fires on mount and on every pan and selection, so the first value stands for what was loaded.
  const hydrated = useRef<number | null>(null)

  useEffect(() => {
    const watcher = new MutationObserver(() => setTheme(pageTheme()))
    watcher.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
    return () => watcher.disconnect()
  }, [])

  return <Guard onFailed={onLoadFailed}>
    <Excalidraw
      initialData={scene}
      theme={theme}
      viewModeEnabled={readOnly}
      UIOptions={UI_OPTIONS}
      validateEmbeddable={false}
      onPaste={(data, event) => {
        const pasted = Array.from(event?.clipboardData?.files ?? [])
        return !Object.keys(data.files ?? {}).length && !pasted.some(file => file.type.startsWith('image/'))
      }}
      onChange={next => {
        const version = versionOf(next)
        if (hydrated.current === null) hydrated.current = version
        if (version === hydrated.current) return
        hydrated.current = version
        onChange(next.filter(element => !element.isDeleted))
      }}
    >
      <MainMenu><MainMenu.DefaultItems.ClearCanvas /></MainMenu>
    </Excalidraw>
  </Guard>
}
