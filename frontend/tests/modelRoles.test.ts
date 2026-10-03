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

  it('prefers the installed official name over a coinstalled alias only when it supports completion', () => {
    const alias = { name: liteAlias, capabilities: ['completion'] };
    const taggedOfficial = {
      name: `${liteModel}:latest`,
      capabilities: ['completion', 'thinking'],
    };
    expect(preferredLiteModel([alias, taggedOfficial])).toBe(taggedOfficial.name);
    expect(preferredLiteModel([alias, { name: liteModel, capabilities: ['completion'] }])).toBe(
      liteModel,
    );
    expect(preferredLiteModel([alias, { ...taggedOfficial, capabilities: ['embedding'] }])).toBe(
      liteAlias,
    );
    expect(preferredLiteModel([{ ...taggedOfficial, capabilities: ['embedding'] }])).toBe(
      liteModel,
    );
  });

  it('distinguishes model sources and non-default tags without implying different weight sizes', () => {
    expect(modelLabel('LiquidAI/lfm2.5-2.6b:latest')).toBe('LFM2.5 · 2.6B · Ollama');
    expect(modelLabel('hf.co/LiquidAI/LFM2.5-2.6B-GGUF:Q4_K_M')).toBe('LFM2.5 · 2.6B · HF import');
    expect(modelLabel(liteAlias)).toBe('LFM2.5 · 2.6B · Local alias');
    expect(modelLabel('LiquidAI/lfm2.5-2.6b:custom')).toBe('LFM2.5 · 2.6B · Ollama · custom');
    expect(modelLabel('hf.co/LiquidAI/LFM2.5-2.6B-GGUF:Q8_0')).toBe(
      'LFM2.5 · 2.6B · HF import · Q8_0',
    );
  });

  it.each([
    'qwen3-embedding:0.6b',
    'hf.co/Other/Custom-GGUF:Q4_K_M',
    'my-LFM2.5-2.6B:experiment',
    'pixel-station-lfm2.5:alternate',
  ])('preserves the unknown model ID %s', (name) => {
    expect(modelLabel(name)).toBe(name);
  });
});
