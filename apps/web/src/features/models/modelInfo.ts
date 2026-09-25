import type { ModelOut } from '../../api/schema';

export function pickRecommended(models: ModelOut[] | undefined): ModelOut | undefined {
  const generative = (models ?? []).filter((m) => !(m.capabilities ?? []).includes('embedding') && !m.id.includes('embedding'));
  return generative.find((m) => m.id === 'local.general.small') ?? generative[0];
}

export function licenseText(model: ModelOut): string {
  const license = model.license;
  if (!license) return 'See model card';
  const name = typeof license.name === 'string' ? license.name : typeof license.id === 'string' ? license.id : 'See model card';
  return license.gated === true ? `${name} (requires accepting the license)` : name;
}
