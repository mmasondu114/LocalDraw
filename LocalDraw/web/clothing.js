"use strict";
// The server supplies the vocabulary; keep this pure resolver in parity with clothing_policy.py.
window.createClothingPolicy = function(rules) {
  const clothing = new RegExp(rules.pattern, "i");
  const has = text => clothing.test(text.replaceAll("_", " "));
  const norm = text => text.replaceAll("_", " ").trim().replace(/\s+/g, " ").toLowerCase();
  const unknown = new RegExp(rules.unknown_pattern || "^(?:未知|unknown\\b)", "i");
  const known = text => !!text && !unknown.test(text);
  function parts(text) {
    const result = []; let start = 0, depth = 0;
    for (let i = 0; i < text.length; i++) {
      if ("([".includes(text[i])) depth++;
      else if (")]".includes(text[i])) depth = Math.max(0, depth - 1);
      else if (",，;；\n".includes(text[i]) && depth === 0) {
        result.push(text.slice(start, i).trim()); start = i + 1;
      }
    }
    result.push(text.slice(start).trim()); return result.filter(Boolean);
  }
  function aliases(card) {
    return [...new Set([card.name,
      ...parts(card.profile?.identity || "").map(s => s.replace(/\s*\([^)]*\)/g, "").trim())])].filter(known);
  }
  const escape = text => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  function sceneClothing(text, card, cards) {
    const owners = new Map();
    for (const other of cards) for (const alias of aliases(other)) {
      const key = alias.toLowerCase();
      if (!owners.has(key)) owners.set(key, new Set());
      owners.get(key).add(other.name);
    }
    if (!owners.size) return has(text);
    const pattern = [...owners.keys()].sort((a,b) => b.length-a.length).map(a =>
      (/^[A-Za-z0-9_]/.test(a) ? "(?<![A-Za-z0-9_])" : "") + escape(a) +
      (/[A-Za-z0-9_]$/.test(a) ? "(?![A-Za-z0-9_])" : "")).join("|");
    for (const sentence of text.split(/[;；。!！?？\n]|\.(?!\d)/)) {
      const mentions = [...sentence.matchAll(new RegExp(pattern, "gi"))];
      if (!mentions.length) {if (has(sentence)) return true; continue;}
      if (has(sentence.slice(0, mentions[0].index))) return true;
      let subjects = new Set();
      for (let i = 0; i < mentions.length; i++) {
        const match = mentions[i];
        for (const name of owners.get(match[0].toLowerCase())) subjects.add(name);
        const clause = sentence.slice(match.index + match[0].length, mentions[i+1]?.index ?? sentence.length);
        if (subjects.has(card.name) && has(clause)) return true;
        if (!/^[\s,/、]*(?:(?:and|和|与|及)[\s,/、]*)?$/i.test(clause)) subjects = new Set();
      }
    }
    return false;
  }
  function resolve(text, prompt, card, cards) {
    const profile = card.profile || {};
    const defaults = [...(known(profile.clothing) ? parts(profile.clothing) : []),
      ...parts(profile.accessories || "").filter(has)];
    const keys = new Set(defaults.map(norm)), tags = parts(prompt);
    const identity = new Set(parts(profile.identity || "").map(norm));
    const isOutfit = p => !identity.has(norm(p)) && (keys.has(norm(p)) || has(p));
    if (sceneClothing(text, card, cards)) return tags.filter(p => !isOutfit(p)).join(", ");
    if (tags.some(p => isOutfit(p) && !keys.has(norm(p)))) return tags.filter(p => !keys.has(norm(p))).join(", ");
    const present = new Set(tags.map(norm)), missing = defaults.filter(p => !present.has(norm(p)));
    return prompt + (missing.length ? (prompt.trim() ? ", " : "") + missing.join(", ") : "");
  }
  return {resolve, parts, has, known};
};
