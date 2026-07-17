/**
 * Display-time taming of ALL-CAPS paper titles ("TOWARDS A METHODOLOGY FOR …" reads like
 * shouting). Pure heuristic, display-only — the stored canonical title is never modified, so
 * search, dedupe and exports are unaffected.
 *
 * Trigger: the title is longer than 10 characters AND >95% of its letters are uppercase
 * (anything less is assumed intentional mixed case and returned untouched, so a normal title
 * containing acronyms is never mangled).
 *
 * Per-word rules once triggered (title case):
 *  - small helper words (articles/conjunctions/prepositions) go lowercase, except at the start
 *    or after sentence-ish punctuation (: ; . ? ! —);
 *  - tokens containing digits keep their case (GPT4, COVID19);
 *  - proper Roman numerals keep caps (II, XIV);
 *  - words of 1–3 letters that are not helper words and not common short English words keep
 *    caps — they are usually acronyms (DNA, SVM, HMM);
 *  - 4–5 letter words keep caps when they are NOT common English words (LSTM, IJCAI stay;
 *    DEEP, MODEL get cased) — a lightweight embedded word list stands in for a dictionary;
 *  - everything else is capitalized (first letter up, rest down).
 *
 * Known limitation: an uncommon long acronym (SIGGRAPH) gets cased like a word, and an unusual
 * 4–5 letter real word missing from the list stays caps. Both are strictly better than the
 * whole title screaming, and the raw title remains one click away in the edit form.
 */

// Helper words kept lowercase in title case (except when a segment starts).
const SMALL_WORDS = new Set([
  'a', 'an', 'the',
  'and', 'but', 'or', 'nor', 'so', 'yet',
  'as', 'at', 'by', 'for', 'in', 'of', 'off', 'on', 'onto', 'out', 'over', 'per', 'to', 'up',
  'upon', 'via', 'vs', 'with', 'within', 'without', 'into', 'from', 'than', 'then', 'when',
  'where', 'while', 'during', 'between', 'through', 'about', 'against', 'among', 'around',
  'before', 'after', 'above', 'below', 'under', 'toward', 'towards', 'along', 'across',
  'behind', 'beyond', 'near', 'is', 'are', 'be', 'its', 'their', 'using', 'do', 'does', 'not',
]);

// Common short (≤5 letters) English words that should be cased normally rather than kept as
// "acronyms". Curated for paper-title vocabulary; anything absent simply stays capitalized.
const COMMON_SHORT_WORDS = new Set([
  // 1–2 letters (pronouns & co — kept out of SMALL_WORDS so they still get capitalized)
  'i', 'we', 'he', 'it', 'my', 'no', 'us', 'me', 'go', 'if', 'am',
  // 3 letters
  'new', 'top', 'low', 'big', 'web', 'map', 'set', 'use', 'way', 'how', 'why', 'who', 'all',
  'can', 'may', 'one', 'two', 'six', 'ten', 'non', 'pre', 'sub', 'key', 'raw', 'red', 'hot',
  'old', 'end', 'era', 'art', 'act', 'age', 'aid', 'air', 'ant', 'bad', 'bag', 'bar', 'bat',
  'bee', 'box', 'boy', 'bus', 'car', 'cat', 'cell', 'cost', 'cut', 'day', 'dog', 'dry', 'ear',
  'eye', 'fat', 'few', 'fit', 'fly', 'fun', 'gap', 'gas', 'gene', 'get', 'gut', 'ice', 'ill',
  'job', 'law', 'lab', 'leg', 'let', 'lie', 'log', 'man', 'men', 'mix', 'net', 'now', 'oil',
  'own', 'pay', 'pen', 'pet', 'pig', 'pot', 'put', 'ray', 'rat', 'row', 'run', 'sea', 'see',
  'sex', 'she', 'sky', 'son', 'sun', 'tax', 'tea', 'tip', 'toe', 'ton', 'toy', 'try', 'war',
  'wet', 'win', 'yes', 'zoo',
  // 4 letters
  'able', 'area', 'back', 'base', 'best', 'bias', 'body', 'bone', 'book', 'born', 'both',
  'brain', 'call', 'card', 'care', 'case', 'city', 'code', 'cold', 'come', 'core', 'cost',
  'data', 'date', 'dead', 'deep', 'does', 'done', 'door', 'down', 'drug', 'dual', 'each',
  'east', 'easy', 'edge', 'else', 'even', 'ever', 'face', 'fact', 'fair', 'fall', 'farm',
  'fast', 'fear', 'feed', 'feel', 'file', 'fill', 'find', 'fine', 'fire', 'firm', 'fish',
  'five', 'flat', 'flow', 'food', 'foot', 'form', 'four', 'free', 'full', 'fund', 'gain',
  'game', 'gene', 'gift', 'girl', 'give', 'goal', 'gold', 'gone', 'good', 'gray', 'grew',
  'grid', 'grow', 'half', 'hall', 'hand', 'hard', 'harm', 'have', 'head', 'hear', 'heat',
  'held', 'help', 'here', 'high', 'hill', 'hold', 'hole', 'home', 'hope', 'hour', 'huge',
  'idea', 'inch', 'iron', 'item', 'join', 'jump', 'just', 'keep', 'kind', 'king', 'knew',
  'know', 'lack', 'lake', 'land', 'lane', 'last', 'late', 'lead', 'lean', 'left', 'less',
  'life', 'lift', 'like', 'line', 'link', 'list', 'live', 'load', 'loan', 'lock', 'long',
  'look', 'lord', 'lose', 'loss', 'lost', 'loud', 'love', 'made', 'mail', 'main', 'make',
  'male', 'many', 'mark', 'mass', 'mean', 'meat', 'meet', 'mind', 'mine', 'miss', 'mode',
  'moon', 'more', 'most', 'move', 'much', 'must', 'name', 'need', 'news', 'next', 'nice',
  'nine', 'node', 'none', 'noon', 'norm', 'nose', 'note', 'once', 'only', 'onto', 'open',
  'oral', 'pace', 'page', 'pain', 'pair', 'park', 'part', 'past', 'path', 'peak', 'pick',
  'pill', 'pipe', 'plan', 'play', 'plot', 'plus', 'poll', 'pool', 'poor', 'port', 'pose',
  'post', 'pull', 'pure', 'push', 'race', 'rain', 'rank', 'rare', 'rate', 'read', 'real',
  'rely', 'rest', 'rich', 'ride', 'ring', 'rise', 'risk', 'road', 'rock', 'role', 'roll',
  'roof', 'room', 'root', 'rose', 'rule', 'safe', 'said', 'salt', 'same', 'sand', 'save',
  'scan', 'seat', 'seed', 'seek', 'seem', 'seen', 'self', 'sell', 'send', 'sent', 'ship',
  'shop', 'shot', 'show', 'shut', 'sick', 'side', 'sign', 'site', 'size', 'skin', 'slow',
  'snow', 'soft', 'soil', 'sold', 'some', 'song', 'soon', 'sort', 'soul', 'spin', 'spot',
  'star', 'stay', 'step', 'stop', 'such', 'suit', 'sure', 'take', 'tale', 'talk', 'tall',
  'tank', 'tape', 'task', 'team', 'tell', 'tend', 'term', 'test', 'text', 'that', 'them',
  'they', 'thin', 'this', 'thus', 'time', 'tiny', 'told', 'tone', 'took', 'tool', 'tour',
  'town', 'tree', 'trip', 'true', 'tube', 'turn', 'twin', 'type', 'unit', 'used', 'user',
  'vary', 'vast', 'very', 'view', 'vote', 'wait', 'walk', 'wall', 'want', 'warm', 'wash',
  'wave', 'weak', 'wear', 'week', 'well', 'went', 'were', 'west', 'what', 'wide', 'wife',
  'wild', 'will', 'wind', 'wine', 'wing', 'wire', 'wise', 'wish', 'wood', 'word', 'wore',
  'work', 'yard', 'year', 'your', 'zero', 'zone',
  // 5 letters (title-frequent)
  'about', 'above', 'agent', 'ahead', 'alarm', 'album', 'alert', 'alive', 'allow', 'alone',
  'apart', 'apple', 'apply', 'arise', 'aware', 'basic', 'basis', 'beach', 'begin', 'being',
  'below', 'bench', 'birth', 'black', 'blind', 'block', 'blood', 'board', 'brain', 'brand',
  'bread', 'break', 'brief', 'bring', 'broad', 'brown', 'build', 'built', 'cache', 'carry',
  'case', 'catch', 'cause', 'chain', 'chair', 'chart', 'cheap', 'check', 'chest', 'chief',
  'child', 'civil', 'claim', 'class', 'clean', 'clear', 'click', 'climb', 'clock', 'close',
  'cloud', 'coach', 'coast', 'color', 'could', 'count', 'court', 'cover', 'crash', 'crime',
  'cross', 'crowd', 'curve', 'cycle', 'daily', 'dance', 'death', 'depth', 'doubt', 'draft',
  'drama', 'dream', 'dress', 'drink', 'drive', 'early', 'earth', 'eight', 'empty', 'enemy',
  'enjoy', 'enter', 'entry', 'equal', 'error', 'event', 'every', 'exact', 'exist', 'extra',
  'faith', 'false', 'fault', 'fiber', 'field', 'fifth', 'fight', 'final', 'first', 'fixed',
  'flash', 'fleet', 'floor', 'fluid', 'focus', 'force', 'forth', 'found', 'frame', 'fresh',
  'front', 'fruit', 'fully', 'fuzzy', 'giant', 'given', 'glass', 'globe', 'going', 'grade',
  'grand', 'grant', 'graph', 'grass', 'great', 'green', 'gross', 'group', 'grow', 'guard',
  'guess', 'guide', 'happy', 'heart', 'heavy', 'hence', 'horse', 'hotel', 'house', 'human',
  'ideal', 'image', 'index', 'inner', 'input', 'issue', 'joint', 'judge', 'known', 'label',
  'large', 'laser', 'later', 'learn', 'least', 'leave', 'legal', 'level', 'light', 'limit',
  'local', 'logic', 'loose', 'lower', 'lucky', 'lunch', 'macro', 'major', 'maker', 'march',
  'match', 'maybe', 'mayor', 'meant', 'media', 'metal', 'micro', 'might', 'minor', 'mixed',
  'model', 'money', 'month', 'moral', 'motor', 'mount', 'mouse', 'mouth', 'movie', 'music',
  'needs', 'never', 'night', 'noise', 'north', 'noted', 'novel', 'nurse', 'occur', 'ocean',
  'offer', 'often', 'order', 'other', 'ought', 'outer', 'owner', 'panel', 'paper', 'party',
  'peace', 'phase', 'phone', 'photo', 'piece', 'pilot', 'pitch', 'place', 'plain', 'plane',
  'plant', 'plate', 'point', 'pound', 'power', 'press', 'price', 'pride', 'prime', 'print',
  'prior', 'prize', 'probe', 'proof', 'proud', 'prove', 'proxy', 'queen', 'query', 'quick',
  'quiet', 'quite', 'radio', 'raise', 'range', 'rapid', 'ratio', 'reach', 'ready', 'refer',
  'right', 'rigid', 'river', 'robot', 'rough', 'round', 'route', 'royal', 'rural', 'scale',
  'scene', 'scope', 'score', 'sense', 'serve', 'seven', 'shall', 'shape', 'share', 'sharp',
  'sheet', 'shelf', 'shell', 'shift', 'shirt', 'shock', 'shoot', 'short', 'shown', 'sight',
  'since', 'sixth', 'sixty', 'sized', 'skill', 'sleep', 'slide', 'small', 'smart', 'smile',
  'smoke', 'solid', 'solve', 'sorry', 'sound', 'south', 'space', 'spare', 'sparse', 'speak',
  'speed', 'spend', 'spent', 'split', 'spoke', 'sport', 'staff', 'stage', 'stand', 'start',
  'state', 'steam', 'steel', 'stick', 'still', 'stock', 'stone', 'store', 'storm', 'story',
  'strip', 'study', 'stuff', 'style', 'sugar', 'suite', 'super', 'sweet', 'table', 'taken',
  'taste', 'teach', 'thank', 'theft', 'there', 'these', 'thick', 'thing', 'think', 'third',
  'those', 'three', 'threw', 'throw', 'tight', 'timed', 'tired', 'title', 'today', 'token',
  'topic', 'total', 'touch', 'tough', 'tower', 'track', 'trade', 'train', 'treat', 'trend',
  'trial', 'tried', 'truck', 'truly', 'trust', 'truth', 'twice', 'under', 'union', 'unity',
  'until', 'upper', 'urban', 'usage', 'usual', 'valid', 'value', 'video', 'virus', 'visit',
  'vital', 'voice', 'waste', 'watch', 'water', 'wheel', 'which', 'white', 'whole', 'whose',
  'woman', 'women', 'world', 'worry', 'worse', 'worst', 'worth', 'would', 'wound', 'write',
  'wrong', 'wrote', 'yield', 'young', 'youth',
]);

// Strict Roman-numeral shape (so DIM/MIX don't match): thousands..units, non-empty, 2+ chars.
const ROMAN_NUMERAL = /^M{0,3}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$/;

function isRomanNumeral(word: string): boolean {
  return word.length >= 2 && ROMAN_NUMERAL.test(word) && /^[IVXLCDM]+$/.test(word);
}

function capitalize(lower: string): string {
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

/** True when the (trimmed, >10 chars) title is ≥95% uppercase letters. */
function isShouting(title: string): boolean {
  if (title.length <= 10) return false;
  const letters = title.replace(/[^a-zA-Z]/g, '');
  // Mostly digits/symbols → not a "shouting" title; leave it alone.
  if (letters.length < 8) return false;
  const upper = letters.replace(/[^A-Z]/g, '').length;
  return upper / letters.length > 0.95;
}

/**
 * Tame an ALL-CAPS title into readable title case for display; returns any non-shouting title
 * unchanged. Never modifies stored data — callers use it at render time only.
 */
export function tameTitle(title: string | null | undefined): string {
  const t = (title ?? '').trim();
  if (!t || !isShouting(t)) return t;

  // Capitalize the very first word and any word following sentence-ish punctuation.
  let forceCap = true;
  return t.replace(/[A-Za-z][A-Za-z']*|[^A-Za-z]+/g, (token) => {
    if (!/^[A-Za-z]/.test(token)) {
      // Separator run: decide whether the NEXT word starts a segment.
      if (/[:;.?!—]/.test(token)) forceCap = true;
      return token;
    }
    const lower = token.toLowerCase();
    const startsSegment = forceCap;
    forceCap = false;

    if (SMALL_WORDS.has(lower)) return startsSegment ? capitalize(lower) : lower;
    // Common-word reading wins over the Roman-numeral one (MIX is a valid numeral, 1009 — but
    // in a title it is almost always the word).
    if (COMMON_SHORT_WORDS.has(lower)) return capitalize(lower);
    if (isRomanNumeral(token)) return token; // II, XIV — keep
    // Short words not in the common-word list are usually acronyms: DNA, SVM, LSTM, IJCAI.
    if (token.length <= 5) return token;
    return capitalize(lower);
  });
}
