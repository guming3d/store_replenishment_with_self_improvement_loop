import { Card, Empty, Space, Table, Tag, Typography } from '@/components/ui';
import { Bar, BarChart, CartesianGrid, Cell, Legend, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { useMemo } from 'react';
import { useStoreContext } from '../App';
import { useI18n } from '../i18n';
import { CHART_SERIES } from '../theme/colors';
import type { Scenario } from '../types';

const { Text, Title } = Typography;
const chartColors = CHART_SERIES;

export default function ScenarioRouting() {
  const { batchResults } = useStoreContext();
  const { t, scenarioLabel } = useI18n();
  const data = useMemo(() => {
    const counts = batchResults.reduce<Record<string, { scenario: Scenario; name: string; count: number; qty: number }>>((acc, item) => {
      if (!acc[item.scenario]) acc[item.scenario] = { scenario: item.scenario, name: scenarioLabel(item.scenario), count: 0, qty: 0 };
      acc[item.scenario].count += 1;
      acc[item.scenario].qty += item.chosen_qty;
      return acc;
    }, {});
    return Object.values(counts);
  }, [batchResults, scenarioLabel]);

  if (!data.length) return <Empty description={t('rt.empty')} />;

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <Card><Title level={3}>{t('rt.title')}</Title><Text type="secondary">{t('rt.subtitle')}</Text></Card>
      <div className="chart-grid">
        <Card title={t('rt.share')}>
          <ResponsiveContainer width="100%" height={320}>
            <PieChart>
              <Pie data={data} dataKey="count" nameKey="name" cx="50%" cy="50%" outerRadius={110} label>
                {data.map((entry, index) => <Cell key={entry.scenario} fill={chartColors[index % chartColors.length]} />)}
              </Pie>
              <Tooltip /><Legend />
            </PieChart>
          </ResponsiveContainer>
        </Card>
        <Card title={t('rt.qtyByScenario')}>
          <ResponsiveContainer width="100%" height={320}>
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" /><XAxis dataKey="name" /><YAxis /><Tooltip /><Legend />
              <Bar dataKey="qty" name={t('rt.recommendedQty')}>{data.map((entry, index) => <Cell key={entry.scenario} fill={chartColors[index % chartColors.length]} />)}</Bar>
            </BarChart>
          </ResponsiveContainer>
        </Card>
      </div>
      <Card title={t('rt.detail')}>
        <Table rowKey="scenario" dataSource={data} pagination={false} columns={[
          { title: t('rt.colScenario'), dataIndex: 'name', render: (value: string) => <Tag color="blue">{value}</Tag> },
          { title: t('rt.colSkuCount'), dataIndex: 'count', align: 'right' },
          { title: t('rt.colTotalQty'), dataIndex: 'qty', align: 'right' },
        ]} />
      </Card>
    </Space>
  );
}
