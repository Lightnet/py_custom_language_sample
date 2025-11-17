#!/usr/bin/env python3
"""
Tiny JIT scripting language – FINAL WORKING VERSION
Features: let, print, variables, +, -, *, /, (), unary -, real parser
Works on llvmlite 0.45+ (including 0.46, 0.47)
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
        self.execution_engine = None
        self.module = None
        self.builder = None
        self.printf = None
        self.variables: Dict[str, ir.AllocaInstr] = {}
        self._fmt_counter = 0  # This fixes the name collision!

    def compile_and_run(self, source: str) -> None:
        self.module = ir.Module()
        self.variables.clear()
        self._fmt_counter = 0

        func_ty = ir.FunctionType(ir.VoidType(), [])
        func = ir.Function(self.module, func_ty, name="main")
        block = func.append_basic_block("entry")
        self.builder = ir.IRBuilder(block)

        self.printf = self._declare_printf()
        self._parse_and_execute(source)
        self.builder.ret_void()

        # Compile & run
        llvm_mod = llvm.parse_assembly(str(self.module))
        llvm_mod.verify()

        backing_mod = llvm.parse_assembly("")
        self.execution_engine = llvm.create_mcjit_compiler(backing_mod, self.target_machine)
        self.execution_engine.add_module(llvm_mod)
        self.execution_engine.finalize_object()
        self.execution_engine.run_static_constructors()

        addr = self.execution_engine.get_function_address("main")
        cfunc = ctypes.CFUNCTYPE(None)(addr)
        cfunc()

    def _declare_printf(self) -> ir.Function:
        void_ptr = ir.IntType(8).as_pointer()
        printf_ty = ir.FunctionType(ir.IntType(32), [void_ptr], var_arg=True)
        return ir.Function(self.module, printf_ty, name="printf")

    def _parse_and_execute(self, source: str):
        lines = [ln.strip() for ln in source.splitlines() if ln.strip()]
        for raw_line in lines:
            line = raw_line.rstrip(";").strip()

            if line.startswith("let "):
                self._parse_let(line[4:])
            elif line.startswith("print("):
                expr = line[6:-1].strip()
                value = self._eval_expr(expr)
                self._print_value(value)
            elif line.startswith("print "):
                expr = line[6:].strip()
                value = self._eval_expr(expr)
                self._print_value(value)
            else:
                # allow bare expressions (e.g. for future side effects)
                self._eval_expr(line)

    def _parse_let(self, text: str):
        if "=" not in text:
            raise SyntaxError(f"Missing '=' in let: {text}")
        name, expr = map(str.strip, text.split("=", 1))
        if not name.isidentifier():
            raise NameError(f"Invalid identifier: {name}")

        value = self._eval_expr(expr)
        alloca = self.builder.alloca(ir.DoubleType(), name=name)
        self.builder.store(value, alloca)
        self.variables[name] = alloca

    def _print_value(self, value: ir.Value):
        fmt = "%.10g\n"
        bytes_val = fmt.encode("utf-8") + b"\0"  # null-terminated

        # Unique name using counter
        fmt_name = f".str.{self._fmt_counter}"
        self._fmt_counter += 1

        arr_ty = ir.ArrayType(ir.IntType(8), len(bytes_val))
        fmt_global = ir.GlobalVariable(self.module, arr_ty, name=fmt_name)
        fmt_global.linkage = "private"
        fmt_global.global_constant = True
        fmt_global.initializer = ir.Constant(arr_ty,
            [ir.Constant(ir.IntType(8), b) for b in bytes_val])

        fmt_ptr = self.builder.gep(fmt_global, [
            ir.Constant(ir.IntType(32), 0),
            ir.Constant(ir.IntType(32), 0)
        ])

        self.builder.call(self.printf, [fmt_ptr, value])

    def _eval_expr(self, expr: str) -> ir.Value:
        expr = expr.replace(" ", "")
        def parse_add(pos=0):
            val, pos = parse_mul(pos)
            while pos < len(expr) and expr[pos] in "+-":
                op = expr[pos]
                pos += 1
                right, pos = parse_mul(pos)
                if op == "+":
                    val = self.builder.fadd(val, right)
                else:
                    val = self.builder.fsub(val, right)
            return val, pos

        def parse_mul(pos=0):
            val, pos = parse_unary(pos)
            while pos < len(expr) and expr[pos] in "*/":
                op = expr[pos]
                pos += 1
                right, pos = parse_unary(pos)
                if op == "*":
                    val = self.builder.fmul(val, right)
                else:
                    val = self.builder.fdiv(val, right)
            return val, pos

        def parse_unary(pos=0):
            if pos < len(expr) and expr[pos] == "-":
                val, pos = parse_primary(pos + 1)
                return self.builder.fsub(ir.Constant(ir.DoubleType(), 0.0), val), pos
            return parse_primary(pos)

        def parse_primary(pos=0):
            if pos >= len(expr):
                raise SyntaxError("Unexpected end of expression")
            c = expr[pos]
            if c == "(":
                val, pos = parse_add(pos + 1)
                if pos >= len(expr) or expr[pos] != ")":
                    raise SyntaxError("Missing ')'")
                return val, pos + 1
            if c.isdigit() or c == ".":
                end = pos
                while end < len(expr) and (expr[end].isdigit() or expr[end] == "."):
                    end += 1
                return ir.Constant(ir.DoubleType(), float(expr[pos:end])), end
            if c.isalpha() or c == "_":
                end = pos
                while end < len(expr) and (expr[end].isalnum() or expr[end] == "_"):
                    end += 1
                name = expr[pos:end]
                if name not in self.variables:
                    raise NameError(f"Undefined variable: {name}")
                return self.builder.load(self.variables[name], name), end
            raise SyntaxError(f"Unexpected character: {c}")

        result, end = parse_add()
        if end != len(expr):
            raise SyntaxError(f"Extra characters: {expr[end:]}")
        return result


# ——— Test ———
if __name__ == "__main__":
    jit = TinyJIT()

    script = """
let a = 1.5;
let b = 1.0;
let c = a + b;
let d = c * 4.0 - 2.5;
print(a);
print(b);
print(c);
print(d);
print(2 + 3 * 4);
print(-5.5);
print((2 + 3) * 4 - 1);
print(3.14159 * 2);
"""

    print("Running script:\n")
    print(script)
    print("Output:")
    print("=" * 40)
    jit.compile_and_run(script)