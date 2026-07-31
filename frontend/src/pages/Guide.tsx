import { PlayCircleOutlined, ReadOutlined } from '@/components/ui/icons';
import { Button, Card, Typography } from '@/components/ui';
import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import MermaidDiagram from '../components/MermaidDiagram';
import { useI18n } from '../i18n';

const { Paragraph, Text, Title } = Typography;

/** Mermaid cannot resolve CSS custom properties, so node styling is emitted as
 *  `classDef` rules built from the resolved token values. The palette is kept to
 *  neutral + brand so the diagram reads like the rest of the UI; the warning hue
 *  is reserved for the branch that genuinely needs attention. */
function roleStyles(tokens: Record<string, string>): string {
  const rule = (fill: string, stroke: string, color: string, width = '1px') =>
    `fill:${fill},stroke:${stroke},stroke-width:${width},color:${color}`;
  return [
    `classDef step ${rule(tokens.surface, tokens.border, tokens['text-primary'])}`,
    `classDef system ${rule(tokens['brand-surface'], tokens['brand-border'], tokens['brand-text'])}`,
    `classDef agent ${rule(tokens['brand-solid'], tokens['brand-solid'], tokens['brand-on-solid'])}`,
    `classDef attention ${rule(tokens['warning-surface'], tokens['warning-border'], tokens['warning-text'])}`,
    `classDef decision ${rule(tokens.surface, tokens['border-strong'], tokens['text-primary'])}`,
  ].join('\n  ');
}

function useIsNarrow(): boolean {
  const [narrow, setNarrow] = useState(
    () => typeof window !== 'undefined' && window.matchMedia('(max-width: 900px)').matches,
  );
  useEffect(() => {
    const mq = window.matchMedia('(max-width: 900px)');
    const handler = () => setNarrow(mq.matches);
    mq.addEventListener('change', handler);
    return () => mq.removeEventListener('change', handler);
  }, []);
  return narrow;
}

export default function Guide() {
  const navigate = useNavigate();
  const { t } = useI18n();
  const narrow = useIsNarrow();

  const dailyFlow = useCallback((tokens: Record<string, string>) => `
flowchart ${narrow ? 'TB' : 'LR'}
  A["${t('guide.flowStart')}"] --> B["${t('guide.flowGenerate')}"]
  B --> C{"${t('guide.flowDecision')}"}
  C -- "${t('guide.flowNo')}" --> G["${t('guide.flowSubmit')}"]
  C -- "${t('guide.flowYes')}" --> D["${t('guide.flowReason')}"]
  D --> E["${t('guide.flowAgent')}"]
  E --> F["${t('guide.flowReview')}"]
  F --> G
  class A,D,F step
  class B,G system
  class E agent
  class C decision
  ${roleStyles(tokens)}
`, [narrow, t]);

  const attributionFlow = useCallback((tokens: Record<string, string>) => `
flowchart ${narrow ? 'TB' : 'LR'}
  A["${t('guide.attrOverride')}"] --> B["${t('guide.attrSnapshot')}"]
  B --> C["${t('guide.attrJudge')}"]
  subgraph AG["${t('guide.attrPluggable')}"]
    direction LR
    D1["${t('guide.attrSeasonal')}"]
    D2["${t('guide.attrSubstitution')}"]
  end
  C --> D1
  C --> D2
  D1 --> E["${t('guide.attrReplay')}"]
  D2 --> E
  E --> F["${t('guide.attrExplained')}"]
  E --> G["${t('guide.attrResidual')}"]
  G --> H["${t('guide.attrManual')}"]
  class A step
  class B,E,F system
  class C,D1,D2 agent
  class G,H attention
  ${roleStyles(tokens)}
`, [narrow, t]);

  return (
    <div className="guide-page">
      <header className="guide-simple-hero">
        <div>
          <div className="guide-eyebrow"><ReadOutlined /> {t('guide.kicker')}</div>
          <Title level={2}>{t('guide.title')}</Title>
          <Paragraph>{t('guide.subtitle')}</Paragraph>
        </div>
        <Button
          type="primary"
          size="large"
          icon={<PlayCircleOutlined />}
          onClick={() => navigate('/suggestions')}
        >
          {t('guide.start')}
        </Button>
      </header>

      <Card className="guide-flow-card">
        <div className="guide-section-heading">
          <div>
            <Title level={3}>{t('guide.flowTitle')}</Title>
            <Text type="secondary">{t('guide.flowSubtitle')}</Text>
          </div>
        </div>
        <MermaidDiagram definition={dailyFlow} description={t('guide.flowDiagramDesc')} />
      </Card>

      <Card className="guide-flow-card">
        <div className="guide-section-heading">
          <div>
            <Title level={3}>{t('guide.attrTitle')}</Title>
            <Text type="secondary">{t('guide.attrSubtitle')}</Text>
          </div>
        </div>
        <MermaidDiagram definition={attributionFlow} description={t('guide.attrDiagramDesc')} />
      </Card>
    </div>
  );
}
