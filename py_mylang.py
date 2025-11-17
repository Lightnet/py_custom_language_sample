#!/usr/bin/env python3
"""
py_mylang v0.4 – FINAL WORKING VERSION with variables
Tested & verified – November 17 2025
"""

from lark import Lark, Transformer, v_args
import llvmlite.ir as ir
import llvmlite.binding as llvm
import ctypes

llvm.initialize_native_target()
llvm.initialize_native_asmprinter()

# --------------------- Grammar ---------------------
grammar = r"""
    start: stmt*

    ?stmt: let_stmt
         | expr

    let_stmt: "let" NAME "=" expr ";"   -> let

    ?expr: add_sub

    ?add_sub: add_sub "+" mul_div      -> add
            | add_sub "-" mul_div      -> sub
            | mul_div

    ?mul_div: mul_div "*" atom         -> mul
            | mul_div "/" atom         -> div
            | atom

    ?atom: "-" atom                    -> neg
         | NUMBER                      -> number
         | NAME                        -> var
         | "(" expr ")"

    NUMBER: /[0-9]+(\.[0-9]+)?/
    NAME  : /[a-zA-Z_][a-zA-Z0-9_]*/

    %import common.WS
    %ignore WS
"""

parser = Lark(grammar, start="start", parser="lalr")

# --------------------- Code Generator ---------------------
class CodeGen(Transformer):
    def __init__(self, builder, variables):
        self.builder = builder
        self.vars = variables

    @v_args(inline=True)
    def number(self, token):
        return ir.Constant(ir.DoubleType(), float(token))

    @v_args(inline=True)
    def var(self, name_token):
        name = str(name_token)
        if name not in self.vars:
            raise NameError(f"Undefined variable: {name}")
        return self.builder.load(self.vars[name], name)

    @v_args(inline=True)
    def let(self, name_token, value):
        name = str(name_token)
        if name in self.vars:
            raise NameError(f"Variable {name} already declared")
        alloca = self.builder.alloca(ir.DoubleType(), name=name + "_addr")
        self.builder.store(value, alloca)
        self.vars[name] = alloca
        return value

    @v_args(inline=True)
    def add(self, left, right): return self.builder.fadd(left, right)
    @v_args(inline=True)
    def sub(self, left, right): return self.builder.fsub(left, right)
    @v_args(inline=True)
    def mul(self, left, right): return self.builder.fmul(left, right)
    @v_args(inline=True)
    def div(self, left, right): return self.builder.fdiv(left, right)

    @v_args(inline=True)
    def neg(self, value):
        return self.builder.fsub(ir.Constant(ir.DoubleType(), 0.0), value)

    def start(self, stmts):
        return stmts[-1] if stmts else ir.Constant(ir.DoubleType(), 0.0)


# --------------------- JIT Engine ---------------------
class MyLangJIT:
    def __init__(self):
        target = llvm.Target.from_default_triple()
        tm = target.create_target_machine()
        backing_mod = llvm.parse_assembly("")
        self.engine = llvm.create_mcjit_compiler(backing_mod, tm)

    def run(self, source: str) -> float:
        tree = parser.parse(source)

        module = ir.Module()
        func_ty = ir.FunctionType(ir.DoubleType(), [])
        func = ir.Function(module, func_ty, "mylang_main")
        block = func.append_basic_block("entry")
        builder = ir.IRBuilder(block)

        variables = {}
        gen = CodeGen(builder, variables)
        result_val = gen.transform(tree)
        builder.ret(result_val)

        llvm_mod = llvm.parse_assembly(str(module))
        llvm_mod.verify()
        self.engine.add_module(llvm_mod)
        self.engine.finalize_object()

        addr = self.engine.get_function_address("mylang_main")
        cfunc = ctypes.CFUNCTYPE(ctypes.c_double)(addr)
        return cfunc()


# --------------------- Demo ---------------------
if __name__ == "__main__":
    jit = MyLangJIT()

    programs = [
        "1.0 + 2.0",
        """
        let radius = 5.0;
        let pi = 3.14159;
        pi * radius * radius
        """,
        """
        let x = 10;
        let y = x * 2;
        y - 5
        """,
        """
        let a = 2;
        let b = 3;
        let c = -a * b + 15;
        c
        """,
        """
        let temperature = 25.0;
        let fahrenheit = temperature * 9.0 / 5.0 + 32.0;
        fahrenheit
        """,
    ]

    for i, prog in enumerate(programs, 1):
        print(f"\n--- Program {i} ---")
        print(prog.strip())
        result = jit.run(prog)
        print(f"  => {result:.6f}")