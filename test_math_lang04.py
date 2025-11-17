#!/usr/bin/env python3
"""
TinyJIT Language – FINAL with WHILE loops
Syntax:
    let x = 0;
    while (x < 5) { print(x); x = x + 1; }
    if (x == 5) { print(999); } else { print(0); }
"""

import llvmlite.ir as ir
import llvmlite.binding as llvm
import ctypes

llvm.initialize_native_target()
llvm.initialize_native_asmprinter()


class TinyJIT:
    def __init__(self):
        self.tm = llvm.Target.from_default_triple().create_target_machine()
        self.module = None
        self.builder = None
        self.printf = None
        self.vars = {}
        self.str_id = 0

    def compile_and_run(self, source: str):
        self.module = ir.Module()
        self.vars.clear()
        self.str_id = 0

        func = ir.Function(self.module, ir.FunctionType(ir.VoidType(), []), "main")
        entry = func.append_basic_block("entry")
        self.builder = ir.IRBuilder(entry)
        self.printf = self._declare_printf()

        for raw_line in source.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("//"):
                continue

            # Handle while and if on single lines
            if line.startswith("while"):
                self._parse_while(line)
            elif line.startswith("if"):
                self._parse_if(line)
            elif line.startswith("let "):
                self._parse_let(line[4:])
            elif line.startswith("print"):
                expr = line[5:].strip("() ")
                self._print_value(self._eval_expr(expr))

        self.builder.ret_void()

        llvm_mod = llvm.parse_assembly(str(self.module))
        llvm_mod.verify()
        ee = llvm.create_mcjit_compiler(llvm.parse_assembly(""), self.tm)
        ee.add_module(llvm_mod)
        ee.finalize_object()
        addr = ee.get_function_address("main")
        ctypes.CFUNCTYPE(None)(addr)()

    def _declare_printf(self):
        ptr = ir.IntType(8).as_pointer()
        return ir.Function(self.module, ir.FunctionType(ir.IntType(32), [ptr], var_arg=True), "printf")

    def _print_value(self, val):
        fmt = "%.10g\n\0".encode()
        gv = ir.GlobalVariable(self.module, ir.ArrayType(ir.IntType(8), len(fmt)),
                               f".str.{self.str_id}")
        self.str_id += 1
        gv.linkage = "private"
        gv.global_constant = True
        gv.initializer = ir.Constant.literal_array([ir.Constant(ir.IntType(8), b) for b in fmt])
        ptr = self.builder.gep(gv, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])
        self.builder.call(self.printf, [ptr, val])

    def _parse_let(self, text: str):
        name, expr = map(str.strip, text.split("=", 1))
        val = self._eval_expr(expr)
        alloca = self.builder.alloca(ir.DoubleType(), name=name)
        self.builder.store(val, alloca)
        self.vars[name] = alloca

    def _parse_while(self, line: str):
        if not line.startswith("while (") or "{" not in line:
            raise SyntaxError("while must be: while (cond) { stmt; stmt; }")
        cond_end = line.find(")")
        body_start = line.find("{") + 1
        body_end = line.rfind("}")
        cond_str = line[line.find("(")+1:cond_end]
        body = line[body_start:body_end]

        loop_bb = self.builder.function.append_basic_block("loop")
        body_bb = self.builder.function.append_basic_block("loop.body")
        exit_bb = self.builder.function.append_basic_block("loop.exit")

        self.builder.branch(loop_bb)
        self.builder.position_at_end(loop_bb)

        cond = self.builder.fcmp_ordered("one",
            self._eval_expr(cond_str), ir.Constant(ir.DoubleType(), 0.0))
        self.builder.cbranch(cond, body_bb, exit_bb)

        self.builder.position_at_end(body_bb)
        for stmt in [s.strip() for s in body.split(";") if s.strip()]:
            if stmt.startswith("print"):
                self._print_value(self._eval_expr(stmt[5:].strip("() ")))
            elif "=" in stmt and stmt.split("=")[0].strip() in self.vars:
                name, expr = map(str.strip, stmt.split("=", 1))
                val = self._eval_expr(expr)
                self.builder.store(val, self.vars[name])
            elif stmt.startswith("let "):
                self._parse_let(stmt)
        self.builder.branch(loop_bb)

        self.builder.position_at_end(exit_bb)

    def _parse_if(self, line: str):
        parts = line.split("} else {")
        if_part = parts[0]
        else_part = parts[1][:-1] if len(parts) > 1 else ""

        cond_start = if_part.find("(") + 1
        cond_end = if_part.find(")")
        body_start = if_part.find("{") + 1
        cond_str = if_part[cond_start:cond_end]
        then_body = if_part[body_start:]

        then_bb = self.builder.function.append_basic_block("then")
        else_bb = self.builder.function.append_basic_block("else")
        merge_bb = self.builder.function.append_basic_block("merge")

        cond = self.builder.fcmp_ordered("one",
            self._eval_expr(cond_str), ir.Constant(ir.DoubleType(), 0.0))
        self.builder.cbranch(cond, then_bb, else_bb if else_part else merge_bb)

        self.builder.position_at_end(then_bb)
        for stmt in [s.strip() for s in then_body.split(";") if s.strip()]:
            self._exec_stmt(stmt)
        self.builder.branch(merge_bb)

        if else_part:
            self.builder.position_at_end(else_bb)
            for stmt in [s.strip() for s in else_part.split(";") if s.strip()]:
                self._exec_stmt(stmt)
            self.builder.branch(merge_bb)

        self.builder.position_at_end(merge_bb)

    def _exec_stmt(self, stmt: str):
        if stmt.startswith("print"):
            self._print_value(self._eval_expr(stmt[5:].strip("() ")))
        elif stmt.startswith("let "):
            self._parse_let(stmt)

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
            if s[p] == "(": v, p = E(p+1); p += 1 if s[p]==")" else None; return v, p
            if s[p].isdigit() or s[p] == ".":
                e = p
                while e < len(s) and (s[e].isdigit() or s[e]=="."): e += 1
                return ir.Constant(ir.DoubleType(), float(s[p:e])), e
            e = p
            while e < len(s) and s[e].isalnum(): e += 1
            name = s[p:e]
            if name not in self.vars: raise NameError(name)
            return self.builder.load(self.vars[name]), e
        v, end = E()
        return v


# ——— FINAL TEST WITH WHILE ———
if __name__ == "__main__":
    jit = TinyJIT()

    script = """
let x = 0;
while (x < 5) { print(x); x = x + 1; }
print(999);
if (x == 5) { print(888); } else { print(0); }
"""

    print("Your language NOW HAS WHILE LOOPS!\n")
    print(script)
    print("Output:")
    print("=" * 50)
    jit.compile_and_run(script)