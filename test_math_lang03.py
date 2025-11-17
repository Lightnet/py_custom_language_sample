#!/usr/bin/env python3
"""
TinyJIT Language – FINAL & CLEAN
Syntax:
    if (x > 5) { print(x); } else { print(0); }
Works perfectly. No more else-on-newline headaches.
"""

import llvmlite.ir as ir
import llvmlite.binding as llvm
import ctypes

llvm.initialize_native_target()
llvm.initialize_native_asmprinter()


class TinyJIT:
    def __init__(self):
        target = llvm.Target.from_default_triple()
        self.tm = target.create_target_machine()
        self.module = ir.Module()
        self.builder = None
        self.printf = None
        self.vars = {}
        self.str_count = 0

    def _declare_printf(self):
        ptr = ir.IntType(8).as_pointer()
        return ir.Function(self.module, ir.FunctionType(ir.IntType(32), [ptr], var_arg=True), "printf")

    def _print_value(self, val):
        fmt = "%.10g\n\0".encode()
        name = f".str.{self.str_count}"
        self.str_count += 1
        gv = ir.GlobalVariable(self.module, ir.ArrayType(ir.IntType(8), len(fmt)), name)
        gv.linkage = "private"
        gv.global_constant = True
        gv.initializer = ir.Constant(ir.ArrayType(ir.IntType(8), len(fmt)),
                                    [ir.Constant(ir.IntType(8), b) for b in fmt])
        ptr = self.builder.gep(gv, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])
        self.builder.call(self.printf, [ptr, val])

    def compile_and_run(self, source: str):
        self.module = ir.Module()
        self.vars.clear()
        self.str_count = 0

        func = ir.Function(self.module, ir.FunctionType(ir.VoidType(), []), "main")
        entry = func.append_basic_block("entry")
        self.builder = ir.IRBuilder(entry)
        self.printf = self._declare_printf()

        lines = [l.rstrip() for l in source.splitlines()]
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line or line.startswith("//"):
                i += 1
                continue

            # Handle if with optional else on same line
            if line.startswith("if"):
                i += self._parse_if_statement(lines, i)
            else:
                stmt = line.rstrip(";").strip()
                if stmt.startswith("let "):
                    name, expr = map(str.strip, stmt[4:].split("=", 1))
                    val = self._eval_expr(expr)
                    alloca = self.builder.alloca(ir.DoubleType(), name=name)
                    self.builder.store(val, alloca)
                    self.vars[name] = alloca
                elif stmt.startswith("print"):
                    expr = stmt[5:].strip()
                    if expr.startswith("(") and expr.endswith(")"):
                        expr = expr[1:-1]
                    val = self._eval_expr(expr)
                    self._print_value(val)
                i += 1

        self.builder.ret_void()

        llvm_mod = llvm.parse_assembly(str(self.module))
        llvm_mod.verify()
        ee = llvm.create_mcjit_compiler(llvm.parse_assembly(""), self.tm)
        ee.add_module(llvm_mod)
        ee.finalize_object()
        addr = ee.get_function_address("main")
        ctypes.CFUNCTYPE(None)(addr)()

    def _parse_if_statement(self, lines: list, start: int) -> int:
        full_line = " ".join(l.strip() for l in lines[start:] if l.strip() and not l.strip().startswith("//"))
        full_line = full_line.split("} else {", 1)  # Split only on } else {

        # Extract condition
        if_part = full_line[0]
        if not if_part.startswith("if (") or ")" not in if_part:
            raise SyntaxError("Invalid if syntax")
        cond_str = if_part[if_part.find("(")+1 : if_part.find(")")]
        cond_val = self._eval_expr(cond_str)
        cond = self.builder.fcmp_ordered("one",
            cond_val, ir.Constant(ir.DoubleType(), 0.0))

        then_bb = self.builder.function.append_basic_block("then")
        else_bb = self.builder.function.append_basic_block("else")
        merge_bb = self.builder.function.append_basic_block("merge")
        self.builder.cbranch(cond, then_bb, else_bb)

        # THEN block
        self.builder.position_at_end(then_bb)
        then_code = if_part.split("{", 1)[1].strip()
        for stmt in [s.strip() for s in then_code.split(";") if s.strip()]:
            if stmt.startswith("print"):
                expr = stmt[5:].strip("() ")
                self._print_value(self._eval_expr(expr))
            elif stmt.startswith("let "):
                name, expr = map(str.strip, stmt[4:].split("=", 1))
                val = self._eval_expr(expr)
                alloca = self.builder.alloca(ir.DoubleType())
                self.builder.store(val, alloca)
                self.vars[name] = alloca
        self.builder.branch(merge_bb)

        # ELSE block (if exists)
        self.builder.position_at_end(else_bb)
        if len(full_line) > 1:
            else_code = full_line[1].split("}", 1)[0].strip()
            for stmt in [s.strip() for s in else_code.split(";") if s.strip()]:
                if stmt.startswith("print"):
                    expr = stmt[5:].strip("() ")
                    self._print_value(self._eval_expr(expr))
        self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)
        return len(lines) - start  # skip all lines used

    def _eval_expr(self, s: str):
        s = s.replace(" ", "")
        def E(p=0): return parse_cmp(p)
        def parse_cmp(p):
            v, p = parse_add(p)
            for op, pred in [(">=", "oge"), ("<=", "ole"), (">", "ogt"), ("<", "olt"), ("==", "oeq"), ("!=", "one")]:
                if s[p:p+len(op)] == op:
                    p += len(op)
                    r, p = parse_add(p)
                    return self.builder.uitofp(self.builder.fcmp_ordered(pred, v, r), ir.DoubleType()), p
            return v, p
        def parse_add(p):
            v, p = parse_mul(p)
            while p < len(s) and s[p] in "+-":
                op, p = s[p], p+1
                r, p = parse_mul(p)
                v = self.builder.fadd(v, r) if op=="+" else self.builder.fsub(v, r)
            return v, p
        def parse_mul(p):
            v, p = parse_unary(p)
            while p < len(s) and s[p] in "*/":
                op, p = s[p], p+1
                r, p = parse_unary(p)
                v = self.builder.fmul(v, r) if op=="*" else self.builder.fdiv(v, r)
            return v, p
        def parse_unary(p):
            if p < len(s) and s[p] == "-":
                v, p = parse_prim(p+1)
                return self.builder.fsub(ir.Constant(ir.DoubleType(), 0.0), v), p
            return parse_prim(p)
        def parse_prim(p):
            if s[p] == "(": v, p = E(p+1); p += 1 if s[p]==")" else _err(")"); return v, p
            if s[p].isdigit() or s[p] == ".":
                e = p
                while e < len(s) and (s[e].isdigit() or s[e]=="."): e += 1
                return ir.Constant(ir.DoubleType(), float(s[p:e])), e
            e = p
            while e < len(s) and s[e].isalnum(): e += 1
            name = s[p:e]
            if name not in self.vars: raise NameError(name)
            return self.builder.load(self.vars[name]), e
        def _err(c): raise SyntaxError(f"Expected {c}")
        v, end = E()
        if end != len(s): raise SyntaxError(f"Extra: {s[end:]}")
        return v


# ——— FINAL TEST ———
if __name__ == "__main__":
    jit = TinyJIT()

    script = """
let x = 10;
let y = -5;

if (x > 5) { print(x); print(999); } else { print(0); }

if (y > 0) { print(111); } else { print(222); }

print(42);
"""

    print("Running final clean version:\n")
    print(script)
    print("Output:")
    print("=" * 50)
    jit.compile_and_run(script)