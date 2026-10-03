import type { Model } from '../../types';

export const liteModel = 'LiquidAI/lfm2.5-2.6b';
export const liteAlias = 'pixel-station-lfm2.5:2.6b';

export function roleCapability(role: string) {
  return role === 'vision' ? 'vision' : role === 'embedding' ? 'embedding' : 'completion';
}

export function acceptsRole(model: Model, role: string) {
  return model.capabilities.includes(roleCapability(role));
}

export function preferredLiteModel(models: Model[]) {
  const official = models.find(
    (model) =>
      (model.name === liteModel || model.name === `${liteModel}:latest`) &&
      acceptsRole(model, 'primary_chat'),
  );
  if (official) return official.name;
  return models.some((model) => model.name === liteAlias && acceptsRole(model, 'primary_chat'))
    ? liteAlias
    : liteModel;
}

export function modelSetupCommand(name: string) {
  return name === liteAlias
    ? `ollama create ${liteAlias} -f config/ollama-lite.Modelfile`
    : `ollama pull ${name}`;
}
