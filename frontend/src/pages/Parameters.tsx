import { Alert, Card, Space, Typography } from '@/components/ui';
import { useStoreContext } from '../App';
import StoreSkuParams from '../components/StoreSkuParams';
import { useI18n } from '../i18n';

const { Text, Title } = Typography;

export default function Parameters() {
  const { shops, selectedShop } = useStoreContext();
  const { t } = useI18n();
  const shopName = shops.find((s) => s.shop_code === selectedShop)?.shop_name ?? selectedShop;

  return (
    <Space direction="vertical" size="large" className="page-stack">
      <Card>
        <Title level={3} style={{ marginBottom: 4 }}>{t('param.title')}</Title>
        <Text type="secondary">{t('param.subtitle')}</Text>
      </Card>

      <Card>
        {selectedShop ? (
          <StoreSkuParams key={selectedShop} shopCode={selectedShop} shopName={shopName} />
        ) : (
          <Alert type="info" showIcon message={t('param.selectShopFirst')} />
        )}
      </Card>
    </Space>
  );
}
