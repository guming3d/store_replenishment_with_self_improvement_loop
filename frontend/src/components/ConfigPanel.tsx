import { QuestionCircleOutlined, SaveOutlined } from '@/components/ui/icons';
import { Alert, Button, Col, Divider, Empty, InputNumber, Row, Space, Spin, Switch, Table, Tag, Tooltip, Typography, message } from '@/components/ui';
import type { ColumnsType } from '@/components/ui';
import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  clearSkuConfig,
  fetchConfigSchema,
  fetchEffectiveConfig,
  saveSkuConfig,
  saveStoreConfig,
} from '../api';
import { useI18n } from '../i18n';
import type { EffectiveConfig, ParamSpec, ReplenishmentParams, Sku } from '../types';

const { Text, Title } = Typography;

interface ConfigPanelProps {
  shopCode: string;
  shopName?: string;
  goodsCode?: string;
  skus?: Sku[];
  onSaved?: () => void;
  /** Show the table of all existing store/SKU overrides (used on the Parameters page). */
  showOverrides?: boolean;
}

const toDisplay = (spec: ParamSpec, val: number): number =>
  spec.type === 'percent' ? Math.round(val * 10000) / 100 : val;
const fromDisplay = (spec: ParamSpec, val: number): number =>
  spec.type === 'percent' ? Math.round((val / 100) * 10000) / 10000 : val;
const precisionOf = (spec: ParamSpec): number => (spec.type === 'int' ? 0 : 2);

function formatValue(spec: ParamSpec, raw: number): string {
  const d = toDisplay(spec, raw);
  return spec.type === 'percent' ? `${d}%` : `${d}`;
}

export default function ConfigPanel({ shopCode, shopName, goodsCode, skus, onSaved, showOverrides }: ConfigPanelProps) {
  const { t, lang } = useI18n();
  const [schema, setSchema] = useState<ParamSpec[]>([]);
  const [effective, setEffective] = useState<EffectiveConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const [storeVals, setStoreVals] = useState<Record<string, number>>({});
  const [skuOverrideOn, setSkuOverrideOn] = useState(false);
  const [skuKeys, setSkuKeys] = useState<Set<string>>(new Set());
  const [skuVals, setSkuVals] = useState<Record<string, number>>({});

  const skuNameMap = useMemo(() => new Map((skus ?? []).map((s) => [s.goods_code, s.goods_name])), [skus]);
  const label = useCallback((p: ParamSpec) => (lang === 'en' ? p.label_en : p.label), [lang]);
  const help = useCallback((p: ParamSpec) => (lang === 'en' ? p.help_en : p.help), [lang]);

  // Scope split: the store form only edits store-scoped params (service level,
  // lead time, review period); SKU overrides only edit sku-scoped params.
  const storeParams = useMemo(() => schema.filter((p) => p.scope === 'store'), [schema]);
  const skuParams = useMemo(() => schema.filter((p) => p.scope === 'sku'), [schema]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [sc, eff] = await Promise.all([fetchConfigSchema(), fetchEffectiveConfig(shopCode, goodsCode)]);
      setSchema(sc.params);
      setEffective(eff);
      const base = eff.store ?? eff.effective;
      const sv: Record<string, number> = {};
      sc.params.forEach((p) => { sv[p.key] = toDisplay(p, Number(base[p.key] ?? p.default)); });
      setStoreVals(sv);

      const kv: Record<string, number> = {};
      sc.params.forEach((p) => {
        const raw = goodsCode && eff.sku && p.key in eff.sku ? eff.sku[p.key] : (base[p.key] ?? p.default);
        kv[p.key] = toDisplay(p, Number(raw));
      });
      setSkuVals(kv);
      setSkuKeys(new Set(goodsCode && eff.sku ? Object.keys(eff.sku) : []));
      setSkuOverrideOn(!!(goodsCode && eff.sku && Object.keys(eff.sku).length > 0));
    } catch {
      message.error(t('cfg.loadFailed'));
    } finally {
      setLoading(false);
    }
  }, [shopCode, goodsCode, t]);

  useEffect(() => { void load(); }, [load]);

  const toggleSkuKey = (key: string, on: boolean) => {
    setSkuKeys((prev) => {
      const next = new Set(prev);
      if (on) next.add(key); else next.delete(key);
      return next;
    });
    if (on) setSkuVals((prev) => ({ ...prev, [key]: prev[key] ?? storeVals[key] }));
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      const storeParamsObj: ReplenishmentParams = {};
      storeParams.forEach((p) => { storeParamsObj[p.key] = fromDisplay(p, Number(storeVals[p.key])); });
      await saveStoreConfig(shopCode, storeParamsObj);

      if (goodsCode) {
        const skuKeySet = new Set(skuParams.map((p) => p.key));
        const active = [...skuKeys].filter((k) => skuKeySet.has(k));
        if (skuOverrideOn && active.length > 0) {
          const skuParamsObj: ReplenishmentParams = {};
          active.forEach((k) => {
            const p = schema.find((s) => s.key === k);
            if (p) skuParamsObj[k] = fromDisplay(p, Number(skuVals[k]));
          });
          await saveSkuConfig(shopCode, goodsCode, skuParamsObj);
        } else {
          await clearSkuConfig(shopCode, goodsCode);
        }
      }
      message.success(t('cfg.saved'));
      await load();
      onSaved?.();
    } catch (e) {
      message.error(t('cfg.saveFailed', { reason: e instanceof Error ? e.message : '' }));
    } finally {
      setSaving(false);
    }
  };

  const handleRemoveOverride = async (gc: string) => {
    try {
      await clearSkuConfig(shopCode, gc);
      message.success(t('cfg.removed'));
      await load();
    } catch {
      message.error(t('cfg.removeFailed'));
    }
  };

  const overrideColumns: ColumnsType<ParamSpec> = [
    { title: t('cfg.colParam'), dataIndex: 'key', render: (_v, p) => (
      <Space size={4}><Text strong>{label(p)}</Text><Tooltip title={help(p)}><QuestionCircleOutlined style={{ color: 'var(--text-muted)' }} /></Tooltip></Space>
    ) },
    { title: t('cfg.colDefault'), key: 'default', align: 'right', render: (_v, p) => <Text type="secondary">{formatValue(p, p.default)}</Text> },
    { title: t('cfg.colOverride'), key: 'toggle', align: 'center', render: (_v, p) => (
      <Switch size="small" checked={skuKeys.has(p.key)} onChange={(on) => toggleSkuKey(p.key, on)} />
    ) },
    { title: t('cfg.colValue'), key: 'value', align: 'right', render: (_v, p) => (
      <InputNumber
        style={{ width: 130 }}
        disabled={!skuKeys.has(p.key)}
        value={skuVals[p.key]}
        min={toDisplay(p, p.min)}
        max={toDisplay(p, p.max)}
        step={p.type === 'percent' ? p.step * 100 : p.step}
        precision={precisionOf(p)}
        addonAfter={p.type === 'percent' ? '%' : undefined}
        onChange={(v) => { if (v != null) setSkuVals((s) => ({ ...s, [p.key]: Number(v) })); }}
      />
    ) },
  ];

  const overrideEntries = useMemo(
    () => Object.entries(effective?.sku_overrides ?? {}),
    [effective],
  );

  if (loading) {
    return <div style={{ padding: 48, textAlign: 'center' }}><Spin tip={t('cfg.loading')} /></div>;
  }

  const levelTag = goodsCode
    ? (effective?.sku ? <Tag color="geekblue">{t('cfg.levelSku')}</Tag>
      : effective?.store ? <Tag color="blue">{t('cfg.levelStore')}</Tag>
        : <Tag>{t('cfg.levelNone')}</Tag>)
    : (effective?.store ? <Tag color="blue">{t('cfg.levelStore')}</Tag> : <Tag>{t('cfg.levelNone')}</Tag>);

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <div>
        <Space align="center" wrap>
          <Title level={5} style={{ margin: 0 }}>{t('cfg.storeTitle')}</Title>
          {levelTag}
        </Space>
        <div><Text type="secondary">{t('cfg.storeHint', { shop: shopName || shopCode })}</Text></div>
      </div>

      <Row gutter={[16, 16]}>
        {storeParams.map((p) => (
          <Col xs={24} sm={12} lg={8} key={p.key}>
            <div style={{ marginBottom: 6 }}>
              <Space size={4}>
                <Text strong>{label(p)}</Text>
                <Tooltip title={help(p)}><QuestionCircleOutlined style={{ color: 'var(--text-muted)' }} /></Tooltip>
              </Space>
            </div>
            <InputNumber
              style={{ width: '100%' }}
              value={storeVals[p.key]}
              min={toDisplay(p, p.min)}
              max={toDisplay(p, p.max)}
              step={p.type === 'percent' ? p.step * 100 : p.step}
              precision={precisionOf(p)}
              addonAfter={p.type === 'percent' ? '%' : undefined}
              onChange={(v) => { if (v != null) setStoreVals((s) => ({ ...s, [p.key]: Number(v) })); }}
            />
          </Col>
        ))}
      </Row>

      {goodsCode && (
        <>
          <Divider style={{ margin: '4px 0' }} />
          <Space align="center" wrap>
            <Title level={5} style={{ margin: 0 }}>
              {t('cfg.skuOverrideTitle', { sku: `${skuNameMap.get(goodsCode) ? `${skuNameMap.get(goodsCode)} · ` : ''}${goodsCode}` })}
            </Title>
            <Switch checked={skuOverrideOn} onChange={setSkuOverrideOn} />
          </Space>
          <Text type="secondary">{t('cfg.skuOverrideHint')}</Text>
          {skuOverrideOn && (
            <Table
              size="small"
              rowKey="key"
              columns={overrideColumns}
              dataSource={skuParams}
              pagination={false}
            />
          )}
        </>
      )}

      {showOverrides && (
        <>
          <Divider style={{ margin: '4px 0' }} />
          <Title level={5} style={{ margin: 0 }}>{t('cfg.overridesTitle')}</Title>
          {overrideEntries.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={t('cfg.noOverrides')} />
          ) : (
            <Table
              size="small"
              rowKey={(row) => row[0]}
              pagination={false}
              dataSource={overrideEntries}
              columns={[
                {
                  title: t('cfg.colSku'),
                  render: (_v, row) => {
                    const gc = row[0];
                    return <Space direction="vertical" size={0}><Text strong>{gc}</Text><Text type="secondary">{skuNameMap.get(gc) ?? ''}</Text></Space>;
                  },
                },
                {
                  title: t('cfg.colOverrides'),
                  render: (_v, row) => {
                    const params = row[1] as ReplenishmentParams;
                    return (
                      <Space wrap>
                        {Object.entries(params).map(([k, val]) => {
                          const p = schema.find((s) => s.key === k);
                          return <Tag key={k} color="geekblue">{p ? label(p) : k}: {p ? formatValue(p, Number(val)) : val}</Tag>;
                        })}
                      </Space>
                    );
                  },
                },
                {
                  title: t('cfg.colOps'),
                  align: 'right',
                  render: (_v, row) => <Button size="small" danger type="link" onClick={() => void handleRemoveOverride(row[0])}>{t('cfg.remove')}</Button>,
                },
              ]}
            />
          )}
        </>
      )}

      {!goodsCode && (
        <Alert type="info" showIcon message={t('cfg.storeScopeNote')} />
      )}

      <div>
        <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={() => void handleSave()}>
          {t('cfg.save')}
        </Button>
      </div>
    </Space>
  );
}
