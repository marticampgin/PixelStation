import { describe, expect, it } from 'vitest';
import {
  acceptsRole,
  liteAlias,
  liteModel,
  preferredLiteModel,
} from '../src/features/settings/modelRoles';
import { modelLabel } from '../src/components/ui';

describe('model routing capabilities', () => {
  it('requires each specialized capability and completion for agent roles', () => {
    const embedding = { name: 'embedding-model', capabilities: ['embedding'] };
    const vision = { name: 'vision-model', capabilities: ['vision', 'completion'] };
    expect(acceptsRole(embedding, 'embedding')).toBe(true);
    expect(acceptsRole(embedding, 'primary_chat')).toBe(false);
    expect(acceptsRole(embedding, 'vision')).toBe(false);
    expect(acceptsRole(vision, 'vision')).toBe(true);
    expect(acceptsRole(vision, 'planner')).toBe(true);
    expect(acceptsRole(vision, 'embedding')).toBe(false);
  });

  it('uses the repaired local Lite alias when present and the official registry model otherwise', () => {
    const imported = {
      name: 'hf.co/LiquidAI/LFM2.5-2.6B-GGUF:Q4_K_M',
      capabilities: ['completion'],
    };
    expect(preferredLiteModel([imported])).toBe(liteModel);
    expect(preferredLiteModel([imported, { name: liteAlias, capabilities: ['completion'] }])).toBe(
      liteAlias,
    );
    expect(modelLabel(liteAlias)).not.toBe(modelLabel(imported.name));
  });
});
