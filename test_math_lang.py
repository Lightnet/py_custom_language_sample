#!/usr/bin/env python3
"""
Tiny arithmetic expression JIT – 100% warning-free on Python 3.8 → 3.14+
"""

import ast
import llvmlite.ir as ir
import llvmlite.binding as llvm
import ctypes

llvm.initialize_native_target()
llvm.initialize_native_asmprinter()


class ExprJIT:
    def __init__(self):
        target = llvm.Target.from_default_triple()
        tm = target.create_target_machine()
        backing_mod = llvm.parse_assembly("")
        self.engine = llvm.create_mcjit_compiler(backing_mod, tm)

    def compile(self, source: str) -> float:
        tree = ast.parse(source, mode="eval")
        return self._codegen(tree.body)

    def _codegen(self, node: ast.AST) -> float:
        module = ir.Module()
        func_ty = ir.FunctionType(ir.DoubleType(), [])
        func = ir.Function(module, func_ty, "jit_expr")
        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        value = self._visit(node, builder)
        builder.ret(value)

        llvm_mod = llvm.parse_assembly(str(module))
        llvm_mod.verify()
        self.engine.add_module(llvm_mod)
        self.engine.finalize_object()

        addr = self.engine.get_function_address("jit_expr")
        cfunc = ctypes.CFUNCTYPE(ctypes.c_double)(addr)
        return cfunc()

    def _visit(self, node: ast.AST, builder: ir.IRBuilder) -> ir.Value:
        # Do not use. ast.Num is deprecated and will be removed in Python 3.14; use ast.Constant instead
        # Modern unified constant (Python 3.8+)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return ir.Constant(ir.DoubleType(), float(node.value))
            raise ValueError(f"Unsupported constant: {node.value!r}")

        if isinstance(node, ast.UnaryOp):
            operand = self._visit(node.operand, builder)
            if isinstance(node.op, ast.USub):
                return builder.fsub(ir.Constant(ir.DoubleType(), 0.0), operand, "neg")
            if isinstance(node.op, ast.UAdd):
                return operand

        if isinstance(node, ast.BinOp):
            left = self._visit(node.left, builder)
            right = self._visit(node.right, builder)

            if isinstance(node.op, ast.Add):
                return builder.fadd(left, right, "addtmp")
            if isinstance(node.op, ast.Sub):
                return builder.fsub(left, right, "subtmp")
            if isinstance(node.op, ast.Mult):
                return builder.fmul(left, right, "multmp")
            if isinstance(node.op, ast.Div):
                return builder.fdiv(left, right, "divtmp")

        if isinstance(node, ast.Expression):
            return self._visit(node.body, builder)

        raise TypeError(f"Unsupported node: {node.__class__.__name__}")


# ——— Test ———
if __name__ == "__main__":
    jit = ExprJIT()
    tests = [
        "1.0 + 2.0",
        "5.0 - 3.2",
        "2.5 * 4.0",
        "10.0 / 3.0",
        "(2 + 3) * 4",
        "2 + 3 * 4",
        "-5.5 + 10",
        "-(2 + 3.5) * 2",
    ]

    for expr in tests:
        result = jit.compile(expr)
        print(f"{expr:30} => {result:.10g}")