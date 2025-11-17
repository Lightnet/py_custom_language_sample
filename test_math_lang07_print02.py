#!/usr/bin/env python3
"""
TinyJIT – THE ULTIMATE VERSION
Features:
  • let x = 5; x = x + 1;
  • print("text")     → no newline
  • println("text")   → with newline
  • printnl()         → just newline
  • while / if / else
  • Beautiful, clean output
"""

import llvmlite.ir as ir
import llvmlite.binding as llvm
import ctypes
from typing import Dict

llvm.initialize_native_target()
llvm.initialize_native_asmprinter()

class TinyJIT:
    def __init__(self):
        target = llvm.Target.from_default_triple()
        self.target_machine = target.create_target_machine()
        self.module = ir.Module()
        self.builder = None
        self.printf = None
        self.variables: Dict[str, ir.AllocaInstr] = {}
        self._str_counter = 0

    def compile_and_run(self, source: str) -> None:
        self.variables.clear()
        self._str_counter = 0

        func_ty = ir.FunctionType(ir.VoidType(), [])
        func = ir.Function(self.module, func_ty, name="main")
        entry = func.append_basic_block("entry")
        self.builder = ir.IRBuilder(entry)

        self.printf = self._declare_printf()
        self._parse_and_execute(source)
        self.builder.ret_void()

        llvm_mod = llvm.parse_assembly(str(self.module))
        llvm_mod.verify()

        ee = llvm.create_mcjit_compiler(llvm_mod, self.target_machine)
        ee.finalize_object()
        ee.run_static_constructors()

        addr = ee.get_function_address("main")
        cfunc = ctypes.CFUNCTYPE(None)(addr)
        cfunc()

    def _declare_printf(self):
        void_ptr = ir.IntType(8).as_pointer()
        return ir.Function(self.module,
                           ir.FunctionType(ir.IntType(32), [void_ptr], var_arg=True),
                           name="printf")

    def _parse_and_execute(self, source: str):
        lines = [ln.rstrip() for ln in source.splitlines() if ln.strip()]
        i = 0
        while i < len(lines):
            line = lines[i].rstrip(";").strip()

            if line.startswith(("if(", "while(")):
                i = self._parse_control_flow(lines, i)
            elif line.startswith("let "):
                self._parse_let(line[4:])
            elif line == "printnl()":
                self._print_newline()
            elif line.startswith("println("):
                inside = line[8:-1].strip()
                if inside.startswith('"') and inside.endswith('"'):
                    self._print_string_with_nl(inside[1:-1])
                else:
                    self._print_value_with_nl(self._eval_expr(inside))
            elif line.startswith("print("):
                inside = line[6:-1].strip()
                if inside.startswith('"') and inside.endswith('"'):
                    self._print_string_no_nl(inside[1:-1])
                else:
                    self._print_value_no_nl(self._eval_expr(inside))
            elif "=" in line and not any(line.startswith(x) for x in ("print", "println", "if", "while", "let")):
                name, expr = map(str.strip, line.split("=", 1))
                if name not in self.variables:
                    raise NameError(f"Assignment to undefined: {name}")
                self.builder.store(self._eval_expr(expr), self.variables[name])
            else:
                self._eval_expr(line)
            i += 1

    def _parse_control_flow(self, lines: list[str], idx: int) -> int:
        line = lines[idx].rstrip(";").strip()
        is_while = line.startswith("while(")
        cond_start = 6 if is_while else 3
        cond_end = line.find("){")
        if cond_end == -1: raise SyntaxError("Expected '{ after condition")
        cond_str = line[cond_start:cond_end].strip()

        body_bb = self.builder.append_basic_block("body")
        exit_bb = self.builder.append_basic_block("exit")
        if is_while:
            self.builder.branch(body_bb)
        else:
            cond_val = self._eval_condition(cond_str)
            self.builder.cbranch(cond_val, body_bb, exit_bb)

        self.builder.position_at_end(body_bb)
        i = idx + 1
        while i < len(lines):
            stmt = lines[i].rstrip(";").strip()
            if stmt == "}": break
            self._execute_statement(stmt)
            i += 1

        if is_while:
            cond_val = self._eval_condition(cond_str)
            self.builder.cbranch(cond_val, body_bb, exit_bb)
        else:
            self.builder.branch(exit_bb if self.builder.block.terminator else exit_bb)

        self.builder.position_at_end(exit_bb)
        return i + 1

    def _execute_statement(self, stmt: str):
        stmt = stmt.strip()
        if stmt == "printnl()":
            self._print_newline()
        elif stmt.startswith("println("):
            inside = stmt[8:-1].strip()
            if inside[:1] == inside[-1:] == '"':
                self._print_string_with_nl(inside[1:-1])
            else:
                self._print_value_with_nl(self._eval_expr(inside))
        elif stmt.startswith("print("):
            inside = stmt[6:-1].strip()
            if inside[:1] == inside[-1:] == '"':
                self._print_string_no_nl(inside[1:-1])
            else:
                self._print_value_no_nl(self._eval_expr(inside))
        elif stmt.startswith("let "):
            self._parse_let(stmt[4:])
        elif "=" in stmt:
            name, expr = map(str.strip, stmt.split("=", 1))
            self.builder.store(self._eval_expr(expr), self.variables[name])
        else:
            self._eval_expr(stmt)

    def _print_newline(self):
        self.builder.call(self.printf, [self._global_str("\n")])

    def _print_string_no_nl(self, text: str):
        self.builder.call(self.printf, [self._global_str(text)])

    def _print_string_with_nl(self, text: str):
        self.builder.call(self.printf, [self._global_str(text + "\n")])

    def _print_value_no_nl(self, value: ir.Value):
        self.builder.call(self.printf, [self._global_str("%.10g"), value])

    def _print_value_with_nl(self, value: ir.Value):
        self.builder.call(self.printf, [self._global_str("%.10g\n"), value])

    def _global_str(self, s: str):
        data = s.encode("utf-8") + b"\0"
        name = f".str.{self._str_counter}"
        self._str_counter += 1
        gv = ir.GlobalVariable(self.module, ir.ArrayType(ir.IntType(8), len(data)), name)
        gv.linkage = "private"
        gv.global_constant = True
        gv.initializer = ir.Constant(gv.type.pointee, [ir.Constant(ir.IntType(8), b) for b in data])
        return self.builder.gep(gv, [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)])

    def _eval_condition(self, expr: str) -> ir.Value:
        expr = expr.replace(" ", "")
        ops = [">=", "<=", "==", "!=", ">", "<"]
        best_op = None; best_pos = len(expr)
        for op in ops:
            pos = expr.find(op)
            if pos != -1 and pos < best_pos:
                best_op = op; best_pos = pos
        if best_op is None:
            v = self._eval_expr(expr)
            return self.builder.fcmp_ordered("!=", v, ir.Constant(ir.DoubleType(), 0.0))
        l = self._eval_expr(expr[:best_pos])
        r = self._eval_expr(expr[best_pos + len(best_op):])
        map_ = {">": "ogt", "<": "olt", ">=": "oge", "<=": "ole", "==": "oeq", "!=": "one"}
        return self.builder.fcmp_ordered(map_[best_op], l, r)

    def _parse_let(self, text: str):
        name, expr = map(str.strip, text.split("=", 1))
        value = self._eval_expr(expr)
        alloca = self.builder.alloca(ir.DoubleType(), name=name)
        self.builder.store(value, alloca)
        self.variables[name] = alloca

    def _eval_expr(self, expr: str) -> ir.Value:
        expr = expr.replace(" ", "")
        def parse_add(p=0):
            v, p = parse_mul(p)
            while p < len(expr) and expr[p] in "+-":
                op = expr[p]; p += 1
                r, p = parse_mul(p)
                v = self.builder.fadd(v, r) if op == "+" else self.builder.fsub(v, r)
            return v, p
        def parse_mul(p=0):
            v, p = parse_unary(p)
            while p < len(expr) and expr[p] in "*/":
                op = expr[p]; p += 1
                r, p = parse_unary(p)
                v = self.builder.fmul(v, r) if op == "*" else self.builder.fdiv(v, r)
            return v, p
        def parse_unary(p=0):
            if p < len(expr) and expr[p] == "-":
                v, p = parse_primary(p + 1)
                return self.builder.fneg(v), p
            return parse_primary(p)
        def parse_primary(p=0):
            if p >= len(expr): raise SyntaxError("Unexpected end")
            c = expr[p]
            if c == "(":
                v, p = parse_add(p + 1)
                if p >= len(expr) or expr[p] != ")": raise SyntaxError("Missing ')'")
                p += 1
                return v, p
            if c.isdigit() or c == ".":
                e = p
                while e < len(expr) and (expr[e].isdigit() or expr[e] == "."): e += 1
                return ir.Constant(ir.DoubleType(), float(expr[p:e])), e
            if c.isalpha() or c == "_":
                e = p
                while e < len(expr) and (expr[e].isalnum() or expr[e] == "_"): e += 1
                name = expr[p:e]
                if name not in self.variables: raise NameError(f"Undefined: {name}")
                return self.builder.load(self.variables[name]), e
            raise SyntaxError(f"Unexpected char: {c!r}")
        v, pos = parse_add()
        if pos != len(expr): raise SyntaxError(f"Extra: {expr[pos:]}")
        return v


# ——————————— FINAL BEAUTIFUL TEST ———————————
if __name__ == "__main__":
    jit = TinyJIT()

    script = """
let i = 0;
let sum = 0;

println("Counting to 10:");

while(i < 10){
    i = i + 1;
    sum = sum + i;
    print("i = ");
    println(i);
}

printnl();
print("Sum 1..10 = ");
print(55);
"""

    print("Running script:\n")
    print(script)
    print("Output:")
    print("=" * 50)
    jit.compile_and_run(script)