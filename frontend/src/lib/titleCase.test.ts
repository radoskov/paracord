import { describe, expect, it } from 'vitest';

import { tameTitle } from './titleCase';

describe('tameTitle — ALL-CAPS titles become readable title case (display only)', () => {
  it('leaves normal mixed-case titles untouched', () => {
    const t = 'Towards a Methodology for Building Ontologies';
    expect(tameTitle(t)).toBe(t);
  });

  it('leaves titles with embedded acronyms untouched (not >95% caps)', () => {
    const t = 'BERT: Pre-training of Deep Bidirectional Transformers';
    expect(tameTitle(t)).toBe(t);
  });

  it('tames a shouting title with small words lowercased', () => {
    expect(tameTitle('TOWARDS A METHODOLOGY FOR BUILDING ONTOLOGIES')).toBe(
      'Towards a Methodology for Building Ontologies',
    );
  });

  it('capitalizes the first word even when it is a small word', () => {
    expect(tameTitle('THE STRUCTURE OF SCIENTIFIC REVOLUTIONS')).toBe(
      'The Structure of Scientific Revolutions',
    );
  });

  it('capitalizes after a colon', () => {
    expect(tameTitle('ATTENTION MODELS: A SURVEY OF THE FIELD')).toBe(
      'Attention Models: A Survey of the Field',
    );
  });

  it('keeps likely acronyms in caps (short words not in the common list)', () => {
    expect(tameTitle('DNA SEQUENCING WITH HMM AND SVM MODELS')).toBe(
      'DNA Sequencing with HMM and SVM Models',
    );
    expect(tameTitle('LSTM NETWORKS FOR LANGUAGE MODELING')).toBe(
      'LSTM Networks for Language Modeling',
    );
  });

  it('cases common short words normally (not treated as acronyms)', () => {
    expect(tameTitle('DEEP LEARNING WITH BIG DATA MODELS')).toBe(
      'Deep Learning with Big Data Models',
    );
  });

  it('keeps proper Roman numerals but not roman-lookalike words', () => {
    expect(tameTitle('WORLD WAR II DOCUMENTS AND ARCHIVES ANALYSIS')).toBe(
      'World War II Documents and Archives Analysis',
    );
    // MIX matches [IVXLCDM]+ but is not a valid numeral shape — it is a common word.
    expect(tameTitle('HOW WE MIX LANGUAGE AND VISION SIGNALS')).toBe(
      'How We MIX Language and Vision Signals'.replace('MIX', 'Mix'),
    );
  });

  it('keeps digit-bearing tokens intact', () => {
    expect(tameTitle('EVALUATING GPT4 ON MEDICAL QUESTION ANSWERING')).toBe(
      'Evaluating GPT4 on Medical Question Answering',
    );
  });

  it('does not touch short all-caps titles (likely a bare acronym)', () => {
    expect(tameTitle('CRISPR')).toBe('CRISPR');
    expect(tameTitle('YOLO V3')).toBe('YOLO V3');
  });

  it('handles empty/null safely', () => {
    expect(tameTitle('')).toBe('');
    expect(tameTitle(null)).toBe('');
    expect(tameTitle(undefined)).toBe('');
  });
});
