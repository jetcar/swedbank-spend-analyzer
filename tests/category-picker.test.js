import { expect, test } from 'vitest';
import { categoryPickerHtml, categoryShortName } from '../app/static/category-picker.js';

test('shows an icon with the first two category letters', () => {
  expect(categoryShortName('транспорт')).toBe('ТР');
  expect(categoryShortName('Épargne')).toBe('ÉP');
  const html = categoryPickerHtml([{id:7,name:'Food',icon:'🍽️'}], 7, {allowEmpty:true,label:'Categories',emptyLabel:'Uncategorized'});
  expect(html).toContain('🍽️');
  expect(html).toContain('<b>FO</b>');
  expect(html).toContain('aria-checked="true"');
  expect(html).toContain('aria-label="Uncategorized"');
});
