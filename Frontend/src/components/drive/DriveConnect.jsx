/**
 * Banner de estado para las fuentes de datos del Dashboard.
 *
 * En modo MVP no hay sesión: solo verificamos que las 3 URLs publicadas
 * estén configuradas como variables de entorno Vite. Si falta alguna,
 * mostramos la lista y la guía rápida para publicar los Sheets.
 */

import { Cloud, CloudOff } from 'lucide-react';
import { Card, CardTitle } from '../ui/Card';
import { missingCsvUrls } from '../../config/drive';

export function DriveConnect() {
  const missing = missingCsvUrls();

  if (missing.length === 0) {
    return (
      <div className="flex items-center gap-3 px-3 py-2 rounded-xl bg-emerald-500/10 border border-emerald-500/25">
        <Cloud size={16} className="text-emerald-300" />
        <span className="text-sm text-emerald-200">
          Conectado a Google Sheets publicados
        </span>
      </div>
    );
  }

  return (
    <Card>
      <CardTitle icon={<CloudOff size={18} />}>
        Fuentes de datos no configuradas
      </CardTitle>
      <div className="text-sm text-slate-300 space-y-3">
        <p>
          Faltan {missing.length} variable{missing.length === 1 ? '' : 's'} de
          entorno con las URLs publicadas de los Google Sheets:
        </p>
        <div className="font-mono text-xs bg-white/[0.03] border border-white/10 rounded-xl p-3 leading-relaxed">
          <div className="text-slate-400"># .env.local (frontend)</div>
          {missing.map((v) => (
            <div key={v} className="text-amber-200">
              {v}=https://docs.google.com/spreadsheets/d/e/.../pub?output=csv
            </div>
          ))}
        </div>
        <p className="text-xs text-slate-400">
          Para obtener cada URL: abrí el Sheet en Drive → Archivo → Compartir →
          Publicar en la web → seleccioná la hoja y formato CSV → Publicar.
          Pegá la URL resultante en la variable correspondiente.
        </p>
      </div>
    </Card>
  );
}
