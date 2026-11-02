/* Resolve whole console request expressions with TypeScript's existing scoped JS parser. */
import fs from 'node:fs';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createRequire } from 'node:module';

const require = createRequire(new URL('../website/package.json', import.meta.url));
const ts = require('typescript');
const { scripts, reviewed } = JSON.parse(fs.readFileSync(0, 'utf8'));
const result = { requests: [], unresolved: [] };

for (const [name, source] of Object.entries(scripts)) {
  const file = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  const options = { allowJs: true, noLib: true, noResolve: true, noEmit: true };
  const host = ts.createCompilerHost(options);
  host.getSourceFile = filename => path.basename(filename) === name ? file : undefined;
  host.readFile = filename => path.basename(filename) === name ? source : undefined;
  host.fileExists = filename => path.basename(filename) === name;
  const program = ts.createProgram([name], options, host), checker = program.getTypeChecker();
  const writes = new Set();
  let callPosition = 0;
  function written(node) {
    if (ts.isIdentifier(node)) {
      const symbol = checker.getSymbolAtLocation(node);
      if (symbol) writes.add(symbol);
    }
    if (ts.isShorthandPropertyAssignment(node)) {
      const symbol = checker.getShorthandAssignmentValueSymbol(node);
      if (symbol) writes.add(symbol);
    }
    ts.forEachChild(node, written);
  }
  function scanWrites(node) {
    if (ts.isBinaryExpression(node) && node.operatorToken.kind >= ts.SyntaxKind.FirstAssignment && node.operatorToken.kind <= ts.SyntaxKind.LastAssignment) written(node.left);
    if ((ts.isPrefixUnaryExpression(node) || ts.isPostfixUnaryExpression(node)) && [ts.SyntaxKind.PlusPlusToken, ts.SyntaxKind.MinusMinusToken].includes(node.operator)) written(node.operand);
    if (ts.isForInStatement(node) || ts.isForOfStatement(node)) written(node.initializer);
    ts.forEachChild(node, scanWrites);
  }
  scanWrites(file);
  function unknown(node, reason) { throw new Error(reason + ': ' + node.getText(file)); }
  function union(...sets) { return new Set(sets.flatMap(set => [...set])); }
  function evaluate(node, bindings = new Map(), visiting = new Set()) {
    if (ts.isStringLiteralLike(node)) return new Set([node.text]);
    if (ts.isParenthesizedExpression(node)) return evaluate(node.expression, bindings, visiting);
    if (node.kind === ts.SyntaxKind.NullKeyword) return new Set([null]);
    if (ts.isConditionalExpression(node)) return union(evaluate(node.whenTrue, bindings, visiting), evaluate(node.whenFalse, bindings, visiting));
    if (ts.isBinaryExpression(node)) {
      const left = evaluate(node.left, bindings, visiting);
      if (node.operatorToken.kind === ts.SyntaxKind.PlusToken) {
        // Once every branch contains '?', later values can only change the query, not the pathname.
        if ([...left].every(value => typeof value === 'string' && value.includes('?'))) return left;
        const right = evaluate(node.right, bindings, visiting);
        return new Set([...left].flatMap(prefix => [...right].map(suffix => prefix + suffix)));
      }
      if (node.operatorToken.kind === ts.SyntaxKind.BarBarToken) {
        const truthy = new Set([...left].filter(Boolean));
        return [...left].every(Boolean) ? truthy : union(truthy, evaluate(node.right, bindings, visiting));
      }
    }
    if (ts.isIdentifier(node)) {
      const symbol = checker.getSymbolAtLocation(node);
      if (bindings.has(symbol)) {
        const argument = bindings.get(symbol);
        return argument ? evaluate(argument, bindings, visiting) : new Set([undefined]);
      }
      const declarations = symbol?.declarations || [];
      if (declarations.length !== 1 || !ts.isVariableDeclaration(declarations[0]) || !declarations[0].initializer || writes.has(symbol)) unknown(node, 'Unresolved or mutable route binding');
      if (declarations[0].initializer.end > callPosition) unknown(node, 'Route bindings must be initialized before use');
      if (visiting.has(symbol)) unknown(node, 'Recursive route binding');
      return evaluate(declarations[0].initializer, bindings, new Set([...visiting, symbol]));
    }
    if (ts.isCallExpression(node)) {
      const symbol = checker.getSymbolAtLocation(node.expression);
      if (ts.isIdentifier(node.expression) && node.expression.text === 'encodeURIComponent' && !symbol?.declarations?.length) {
        if (node.arguments.length !== 1) unknown(node, 'Encoded route IDs need one argument');
        // Encoding makes an unknown ID one opaque path segment; literals retain their exact value.
        if (ts.isStringLiteralLike(node.arguments[0])) return new Set([encodeURIComponent(node.arguments[0].text)]);
        return new Set(['{id}']);
      }
      const declarations = symbol?.declarations || [];
      if (declarations.length !== 1 || !ts.isFunctionDeclaration(declarations[0]) || writes.has(symbol)) unknown(node, 'Unresolved route helper');
      const helper = declarations[0];
      if (!helper.body || helper.body.statements.length !== 1 || !ts.isReturnStatement(helper.body.statements[0]) || !helper.body.statements[0].expression) unknown(node, 'Route helpers need one resolvable return');
      if (node.arguments.length > helper.parameters.length || visiting.has(symbol)) unknown(node, 'Unresolved route helper arguments');
      const argumentsBySymbol = new Map(bindings);
      helper.parameters.forEach((parameter, index) => {
        if (!ts.isIdentifier(parameter.name) || parameter.dotDotDotToken || parameter.initializer) unknown(parameter, 'Unresolved helper parameter');
        argumentsBySymbol.set(checker.getSymbolAtLocation(parameter.name), node.arguments[index]);
      });
      return evaluate(helper.body.statements[0].expression, argumentsBySymbol, new Set([...visiting, symbol]));
    }
    unknown(node, 'Unresolved route expression');
  }
  function visit(node) {
    const callee = ts.isCallExpression(node) ? node.expression : null;
    if (callee && ts.isPropertyAccessExpression(callee) && ts.isIdentifier(callee.expression) && callee.expression.text === 'M' && ['api', 'rawApi'].includes(callee.name.text)) {
      const method = node.arguments[0], argument = node.arguments[1];
      if (!method || !ts.isStringLiteralLike(method) || !argument) {
        result.unresolved.push({ name, expression: node.getText(file), reason: 'Use a literal request method and a resolvable path' });
      } else {
        let paths;
        callPosition = node.getStart(file);
        try { paths = evaluate(argument); }
        catch (error) {
          const exception = reviewed[name], key = method.text + ' ' + argument.getText(file);
          const unchanged = exception && createHash('sha256').update(source).digest('hex') === exception.sha256;
          if (unchanged && Object.hasOwn(exception.paths, key)) paths = new Set(exception.paths[key]);
          else result.unresolved.push({ name, expression: argument.getText(file), reason: error.message });
        }
        if (paths) {
          for (const route of paths) {
            if (typeof route !== 'string') result.unresolved.push({ name, expression: argument.getText(file), reason: 'The request path must be a string' });
            else result.requests.push([method.text, route.split('?')[0]]);
          }
        }
      }
    }
    ts.forEachChild(node, visit);
  }
  const errors = program.getSyntacticDiagnostics(file);
  if (errors.length) result.unresolved.push({ name, reason: ts.flattenDiagnosticMessageText(errors[0].messageText, '\n') });
  else visit(file);
}
console.log(JSON.stringify(result));
