/**
 * Página Configuración: tabs para las 5 secciones de bot_config.
 *
 * GET /api/config trae todo de una vez en el bootstrap; cada PUT individual
 * actualiza la sección correspondiente sin invalidar las demás.
 */

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Timer,
  Sparkles,
  Clock,
  Megaphone,
  Settings,
} from 'lucide-react';
import { fetchAllConfig } from '../api/client';
import { Card, CardTitle } from '../components/ui/Card';
import { ErrorCard, Loading } from '../components/ui/EmptyState';
import { Tabs } from '../components/ui/Tabs';
import {
  AnthropicForm,
  DispatcherForm,
  OperatingHoursForm,
} from '../components/config/ConfigForms';
import { BroadcastsPage } from './Broadcasts';

const TABS = [
  { value: 'dispatcher', label: 'Dispatcher', icon: <Timer size={14} /> },
  { value: 'anthropic', label: 'Anthropic', icon: <Sparkles size={14} /> },
  { value: 'operating_hours', label: 'Horarios', icon: <Clock size={14} /> },
  { value: 'broadcasts', label: 'Broadcasts', icon: <Megaphone size={14} /> },
];

export function ConfiguracionPage() {
  const [tab, setTab] = useState('dispatcher');

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['config'],
    queryFn: fetchAllConfig,
    staleTime: 30_000,
  });

  if (isLoading) return <Loading label="Cargando configuración…" />;
  if (isError) return <ErrorCard error={error} />;

  return (
    <div className="anim-fadein">
      <Tabs value={tab} onChange={setTab} tabs={TABS} />

      {tab === 'broadcasts' ? (
        <BroadcastsPage />
      ) : (
        <Card>
          <CardTitle icon={<Settings size={18} />}>
            {TABS.find((t) => t.value === tab)?.label}
          </CardTitle>

          {tab === 'dispatcher' && <DispatcherForm initial={data.dispatcher} />}
          {tab === 'anthropic' && <AnthropicForm initial={data.anthropic} />}
          {tab === 'operating_hours' && (
            <OperatingHoursForm initial={data.operating_hours} />
          )}
        </Card>
      )}
    </div>
  );
}
