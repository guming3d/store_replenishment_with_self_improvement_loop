import { useEffect, useId, useState } from 'react';

/**
 * Mermaid renders to inline SVG using literal colour values, so CSS custom
 * properties cannot be referenced directly. The token values are therefore
 * resolved from the live theme element on every render, and the diagram is
 * re-rendered whenever the root appearance class changes.
 */
type Tokens = Record<string, string>;

const TOKEN_NAMES = [
  'surface', 'bg-subtle', 'border', 'border-strong',
  'text-primary', 'text-secondary', 'text-inverse',
  'brand-solid', 'brand-surface', 'brand-border', 'brand-text', 'brand-on-solid',
  'success-solid', 'success-surface', 'success-border', 'success-text',
  'warning-solid', 'warning-surface', 'warning-border', 'warning-text',
  'danger-surface', 'danger-border', 'danger-text',
] as const;

function themeElement(): Element {
  return document.querySelector('.radix-themes[data-is-root-theme="true"]')
    ?? document.documentElement;
}

function readTokens(): Tokens {
  const computed = getComputedStyle(themeElement());
  const tokens: Tokens = {};
  for (const name of TOKEN_NAMES) {
    tokens[name] = computed.getPropertyValue(`--${name}`).trim() || '#888888';
  }
  return tokens;
}

export interface MermaidDiagramProps {
  /** Mermaid definition. Receives resolved design tokens so `classDef` rules
   *  can use real colours rather than unresolvable `var()` references. */
  definition: (tokens: Tokens) => string;
  /** Text alternative; the SVG itself is decorative to screen readers. */
  description: string;
  className?: string;
}

export default function MermaidDiagram({
  definition, description, className,
}: MermaidDiagramProps) {
  const rawId = useId();
  const domId = `mermaid-${rawId.replace(/[^a-zA-Z0-9]/g, '')}`;
  const [svg, setSvg] = useState('');
  const [failed, setFailed] = useState(false);
  // Bumped by the observer below so a theme switch re-runs the render effect.
  const [appearance, setAppearance] = useState(0);

  useEffect(() => {
    const observer = new MutationObserver(() => setAppearance((n) => n + 1));
    observer.observe(themeElement(), { attributes: true, attributeFilter: ['class'] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    const tokens = readTokens();

    // Imported lazily so the large mermaid bundle is only fetched on pages that
    // actually render a diagram.
    import('mermaid').then(({ default: mermaid }) => {
      if (cancelled) return undefined;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'base',
        fontFamily: getComputedStyle(document.body).fontFamily,
        flowchart: {
          curve: 'basis', htmlLabels: true, padding: 10,
          nodeSpacing: 24, rankSpacing: 26, useMaxWidth: false,
        },
        themeVariables: {
          background: 'transparent',
          primaryColor: tokens['brand-surface'],
          primaryTextColor: tokens['text-primary'],
          primaryBorderColor: tokens['brand-border'],
          secondaryColor: tokens['bg-subtle'],
          tertiaryColor: tokens.surface,
          mainBkg: tokens['brand-surface'],
          nodeBorder: tokens['brand-border'],
          lineColor: tokens['border-strong'],
          textColor: tokens['text-primary'],
          clusterBkg: tokens['bg-subtle'],
          clusterBorder: tokens.border,
          edgeLabelBackground: tokens.surface,
          fontSize: '15px',
        },
      });
      return mermaid.render(domId, definition(tokens));
    })
      .then((result) => {
        if (!cancelled && result) { setSvg(result.svg); setFailed(false); }
      })
      .catch(() => { if (!cancelled) setFailed(true); });

    return () => { cancelled = true; };
  }, [definition, domId, appearance]);

  if (failed) {
    return <p className={`mermaid-fallback ${className ?? ''}`}>{description}</p>;
  }

  return (
    <div
      className={`mermaid-diagram ${className ?? ''}`}
      role="img"
      aria-label={description}
      // Mermaid output is sanitised by its own strict security level.
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}
