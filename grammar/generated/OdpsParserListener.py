# Copyright (c) 2024-2026, Alibaba Cloud and its affiliates.
# SPDX-License-Identifier: Apache-2.0
#
# Generated from Apache-2.0 MaxCompute grammar sourced from
# aliyun/aliyun-odps-java-sdk; see grammar/README.md.

# Generated from grammar/odps/OdpsParser.g4 by ANTLR 4.13.2
from antlr4 import *
if "." in __name__:
    from .OdpsParser import OdpsParser
else:
    from OdpsParser import OdpsParser

# This class defines a complete listener for a parse tree produced by OdpsParser.
class OdpsParserListener(ParseTreeListener):

    # Enter a parse tree produced by OdpsParser#script.
    def enterScript(self, ctx:OdpsParser.ScriptContext):
        pass

    # Exit a parse tree produced by OdpsParser#script.
    def exitScript(self, ctx:OdpsParser.ScriptContext):
        pass


    # Enter a parse tree produced by OdpsParser#userCodeBlock.
    def enterUserCodeBlock(self, ctx:OdpsParser.UserCodeBlockContext):
        pass

    # Exit a parse tree produced by OdpsParser#userCodeBlock.
    def exitUserCodeBlock(self, ctx:OdpsParser.UserCodeBlockContext):
        pass


    # Enter a parse tree produced by OdpsParser#statement.
    def enterStatement(self, ctx:OdpsParser.StatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#statement.
    def exitStatement(self, ctx:OdpsParser.StatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#compoundStatement.
    def enterCompoundStatement(self, ctx:OdpsParser.CompoundStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#compoundStatement.
    def exitCompoundStatement(self, ctx:OdpsParser.CompoundStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#emptyStatement.
    def enterEmptyStatement(self, ctx:OdpsParser.EmptyStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#emptyStatement.
    def exitEmptyStatement(self, ctx:OdpsParser.EmptyStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#execStatement.
    def enterExecStatement(self, ctx:OdpsParser.ExecStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#execStatement.
    def exitExecStatement(self, ctx:OdpsParser.ExecStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#cteStatement.
    def enterCteStatement(self, ctx:OdpsParser.CteStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#cteStatement.
    def exitCteStatement(self, ctx:OdpsParser.CteStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableAliasWithCols.
    def enterTableAliasWithCols(self, ctx:OdpsParser.TableAliasWithColsContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableAliasWithCols.
    def exitTableAliasWithCols(self, ctx:OdpsParser.TableAliasWithColsContext):
        pass


    # Enter a parse tree produced by OdpsParser#subQuerySource.
    def enterSubQuerySource(self, ctx:OdpsParser.SubQuerySourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#subQuerySource.
    def exitSubQuerySource(self, ctx:OdpsParser.SubQuerySourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#explainStatement.
    def enterExplainStatement(self, ctx:OdpsParser.ExplainStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#explainStatement.
    def exitExplainStatement(self, ctx:OdpsParser.ExplainStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#ifStatement.
    def enterIfStatement(self, ctx:OdpsParser.IfStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#ifStatement.
    def exitIfStatement(self, ctx:OdpsParser.IfStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#loopStatement.
    def enterLoopStatement(self, ctx:OdpsParser.LoopStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#loopStatement.
    def exitLoopStatement(self, ctx:OdpsParser.LoopStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#functionDefinition.
    def enterFunctionDefinition(self, ctx:OdpsParser.FunctionDefinitionContext):
        pass

    # Exit a parse tree produced by OdpsParser#functionDefinition.
    def exitFunctionDefinition(self, ctx:OdpsParser.FunctionDefinitionContext):
        pass


    # Enter a parse tree produced by OdpsParser#functionParameters.
    def enterFunctionParameters(self, ctx:OdpsParser.FunctionParametersContext):
        pass

    # Exit a parse tree produced by OdpsParser#functionParameters.
    def exitFunctionParameters(self, ctx:OdpsParser.FunctionParametersContext):
        pass


    # Enter a parse tree produced by OdpsParser#parameterDefinition.
    def enterParameterDefinition(self, ctx:OdpsParser.ParameterDefinitionContext):
        pass

    # Exit a parse tree produced by OdpsParser#parameterDefinition.
    def exitParameterDefinition(self, ctx:OdpsParser.ParameterDefinitionContext):
        pass


    # Enter a parse tree produced by OdpsParser#typeDeclaration.
    def enterTypeDeclaration(self, ctx:OdpsParser.TypeDeclarationContext):
        pass

    # Exit a parse tree produced by OdpsParser#typeDeclaration.
    def exitTypeDeclaration(self, ctx:OdpsParser.TypeDeclarationContext):
        pass


    # Enter a parse tree produced by OdpsParser#parameterTypeDeclaration.
    def enterParameterTypeDeclaration(self, ctx:OdpsParser.ParameterTypeDeclarationContext):
        pass

    # Exit a parse tree produced by OdpsParser#parameterTypeDeclaration.
    def exitParameterTypeDeclaration(self, ctx:OdpsParser.ParameterTypeDeclarationContext):
        pass


    # Enter a parse tree produced by OdpsParser#functionTypeDeclaration.
    def enterFunctionTypeDeclaration(self, ctx:OdpsParser.FunctionTypeDeclarationContext):
        pass

    # Exit a parse tree produced by OdpsParser#functionTypeDeclaration.
    def exitFunctionTypeDeclaration(self, ctx:OdpsParser.FunctionTypeDeclarationContext):
        pass


    # Enter a parse tree produced by OdpsParser#parameterTypeDeclarationList.
    def enterParameterTypeDeclarationList(self, ctx:OdpsParser.ParameterTypeDeclarationListContext):
        pass

    # Exit a parse tree produced by OdpsParser#parameterTypeDeclarationList.
    def exitParameterTypeDeclarationList(self, ctx:OdpsParser.ParameterTypeDeclarationListContext):
        pass


    # Enter a parse tree produced by OdpsParser#parameterColumnNameTypeList.
    def enterParameterColumnNameTypeList(self, ctx:OdpsParser.ParameterColumnNameTypeListContext):
        pass

    # Exit a parse tree produced by OdpsParser#parameterColumnNameTypeList.
    def exitParameterColumnNameTypeList(self, ctx:OdpsParser.ParameterColumnNameTypeListContext):
        pass


    # Enter a parse tree produced by OdpsParser#parameterColumnNameType.
    def enterParameterColumnNameType(self, ctx:OdpsParser.ParameterColumnNameTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#parameterColumnNameType.
    def exitParameterColumnNameType(self, ctx:OdpsParser.ParameterColumnNameTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#varSizeParam.
    def enterVarSizeParam(self, ctx:OdpsParser.VarSizeParamContext):
        pass

    # Exit a parse tree produced by OdpsParser#varSizeParam.
    def exitVarSizeParam(self, ctx:OdpsParser.VarSizeParamContext):
        pass


    # Enter a parse tree produced by OdpsParser#assignStatement.
    def enterAssignStatement(self, ctx:OdpsParser.AssignStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#assignStatement.
    def exitAssignStatement(self, ctx:OdpsParser.AssignStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#preSelectClauses.
    def enterPreSelectClauses(self, ctx:OdpsParser.PreSelectClausesContext):
        pass

    # Exit a parse tree produced by OdpsParser#preSelectClauses.
    def exitPreSelectClauses(self, ctx:OdpsParser.PreSelectClausesContext):
        pass


    # Enter a parse tree produced by OdpsParser#postSelectClauses.
    def enterPostSelectClauses(self, ctx:OdpsParser.PostSelectClausesContext):
        pass

    # Exit a parse tree produced by OdpsParser#postSelectClauses.
    def exitPostSelectClauses(self, ctx:OdpsParser.PostSelectClausesContext):
        pass


    # Enter a parse tree produced by OdpsParser#selectRest.
    def enterSelectRest(self, ctx:OdpsParser.SelectRestContext):
        pass

    # Exit a parse tree produced by OdpsParser#selectRest.
    def exitSelectRest(self, ctx:OdpsParser.SelectRestContext):
        pass


    # Enter a parse tree produced by OdpsParser#multiInsertFromRest.
    def enterMultiInsertFromRest(self, ctx:OdpsParser.MultiInsertFromRestContext):
        pass

    # Exit a parse tree produced by OdpsParser#multiInsertFromRest.
    def exitMultiInsertFromRest(self, ctx:OdpsParser.MultiInsertFromRestContext):
        pass


    # Enter a parse tree produced by OdpsParser#fromRest.
    def enterFromRest(self, ctx:OdpsParser.FromRestContext):
        pass

    # Exit a parse tree produced by OdpsParser#fromRest.
    def exitFromRest(self, ctx:OdpsParser.FromRestContext):
        pass


    # Enter a parse tree produced by OdpsParser#simpleQueryExpression.
    def enterSimpleQueryExpression(self, ctx:OdpsParser.SimpleQueryExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#simpleQueryExpression.
    def exitSimpleQueryExpression(self, ctx:OdpsParser.SimpleQueryExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#selectQueryExpression.
    def enterSelectQueryExpression(self, ctx:OdpsParser.SelectQueryExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#selectQueryExpression.
    def exitSelectQueryExpression(self, ctx:OdpsParser.SelectQueryExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#fromQueryExpression.
    def enterFromQueryExpression(self, ctx:OdpsParser.FromQueryExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#fromQueryExpression.
    def exitFromQueryExpression(self, ctx:OdpsParser.FromQueryExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#setOperationFactor.
    def enterSetOperationFactor(self, ctx:OdpsParser.SetOperationFactorContext):
        pass

    # Exit a parse tree produced by OdpsParser#setOperationFactor.
    def exitSetOperationFactor(self, ctx:OdpsParser.SetOperationFactorContext):
        pass


    # Enter a parse tree produced by OdpsParser#queryExpression.
    def enterQueryExpression(self, ctx:OdpsParser.QueryExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#queryExpression.
    def exitQueryExpression(self, ctx:OdpsParser.QueryExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#queryExpressionWithCTE.
    def enterQueryExpressionWithCTE(self, ctx:OdpsParser.QueryExpressionWithCTEContext):
        pass

    # Exit a parse tree produced by OdpsParser#queryExpressionWithCTE.
    def exitQueryExpressionWithCTE(self, ctx:OdpsParser.QueryExpressionWithCTEContext):
        pass


    # Enter a parse tree produced by OdpsParser#setRHS.
    def enterSetRHS(self, ctx:OdpsParser.SetRHSContext):
        pass

    # Exit a parse tree produced by OdpsParser#setRHS.
    def exitSetRHS(self, ctx:OdpsParser.SetRHSContext):
        pass


    # Enter a parse tree produced by OdpsParser#multiInsertSetOperationFactor.
    def enterMultiInsertSetOperationFactor(self, ctx:OdpsParser.MultiInsertSetOperationFactorContext):
        pass

    # Exit a parse tree produced by OdpsParser#multiInsertSetOperationFactor.
    def exitMultiInsertSetOperationFactor(self, ctx:OdpsParser.MultiInsertSetOperationFactorContext):
        pass


    # Enter a parse tree produced by OdpsParser#multiInsertSelect.
    def enterMultiInsertSelect(self, ctx:OdpsParser.MultiInsertSelectContext):
        pass

    # Exit a parse tree produced by OdpsParser#multiInsertSelect.
    def exitMultiInsertSelect(self, ctx:OdpsParser.MultiInsertSelectContext):
        pass


    # Enter a parse tree produced by OdpsParser#multiInsertSetRHS.
    def enterMultiInsertSetRHS(self, ctx:OdpsParser.MultiInsertSetRHSContext):
        pass

    # Exit a parse tree produced by OdpsParser#multiInsertSetRHS.
    def exitMultiInsertSetRHS(self, ctx:OdpsParser.MultiInsertSetRHSContext):
        pass


    # Enter a parse tree produced by OdpsParser#multiInsertBranch.
    def enterMultiInsertBranch(self, ctx:OdpsParser.MultiInsertBranchContext):
        pass

    # Exit a parse tree produced by OdpsParser#multiInsertBranch.
    def exitMultiInsertBranch(self, ctx:OdpsParser.MultiInsertBranchContext):
        pass


    # Enter a parse tree produced by OdpsParser#fromStatement.
    def enterFromStatement(self, ctx:OdpsParser.FromStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#fromStatement.
    def exitFromStatement(self, ctx:OdpsParser.FromStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#insertStatement.
    def enterInsertStatement(self, ctx:OdpsParser.InsertStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#insertStatement.
    def exitInsertStatement(self, ctx:OdpsParser.InsertStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#selectQueryStatement.
    def enterSelectQueryStatement(self, ctx:OdpsParser.SelectQueryStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#selectQueryStatement.
    def exitSelectQueryStatement(self, ctx:OdpsParser.SelectQueryStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#queryStatement.
    def enterQueryStatement(self, ctx:OdpsParser.QueryStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#queryStatement.
    def exitQueryStatement(self, ctx:OdpsParser.QueryStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#insertStatementWithCTE.
    def enterInsertStatementWithCTE(self, ctx:OdpsParser.InsertStatementWithCTEContext):
        pass

    # Exit a parse tree produced by OdpsParser#insertStatementWithCTE.
    def exitInsertStatementWithCTE(self, ctx:OdpsParser.InsertStatementWithCTEContext):
        pass


    # Enter a parse tree produced by OdpsParser#subQueryExpression.
    def enterSubQueryExpression(self, ctx:OdpsParser.SubQueryExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#subQueryExpression.
    def exitSubQueryExpression(self, ctx:OdpsParser.SubQueryExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#limitClause.
    def enterLimitClause(self, ctx:OdpsParser.LimitClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#limitClause.
    def exitLimitClause(self, ctx:OdpsParser.LimitClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#fromSource.
    def enterFromSource(self, ctx:OdpsParser.FromSourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#fromSource.
    def exitFromSource(self, ctx:OdpsParser.FromSourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableVariableSource.
    def enterTableVariableSource(self, ctx:OdpsParser.TableVariableSourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableVariableSource.
    def exitTableVariableSource(self, ctx:OdpsParser.TableVariableSourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableFunctionSource.
    def enterTableFunctionSource(self, ctx:OdpsParser.TableFunctionSourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableFunctionSource.
    def exitTableFunctionSource(self, ctx:OdpsParser.TableFunctionSourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#createMachineLearningModelStatment.
    def enterCreateMachineLearningModelStatment(self, ctx:OdpsParser.CreateMachineLearningModelStatmentContext):
        pass

    # Exit a parse tree produced by OdpsParser#createMachineLearningModelStatment.
    def exitCreateMachineLearningModelStatment(self, ctx:OdpsParser.CreateMachineLearningModelStatmentContext):
        pass


    # Enter a parse tree produced by OdpsParser#variableName.
    def enterVariableName(self, ctx:OdpsParser.VariableNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#variableName.
    def exitVariableName(self, ctx:OdpsParser.VariableNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#atomExpression.
    def enterAtomExpression(self, ctx:OdpsParser.AtomExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#atomExpression.
    def exitAtomExpression(self, ctx:OdpsParser.AtomExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#variableRef.
    def enterVariableRef(self, ctx:OdpsParser.VariableRefContext):
        pass

    # Exit a parse tree produced by OdpsParser#variableRef.
    def exitVariableRef(self, ctx:OdpsParser.VariableRefContext):
        pass


    # Enter a parse tree produced by OdpsParser#variableCall.
    def enterVariableCall(self, ctx:OdpsParser.VariableCallContext):
        pass

    # Exit a parse tree produced by OdpsParser#variableCall.
    def exitVariableCall(self, ctx:OdpsParser.VariableCallContext):
        pass


    # Enter a parse tree produced by OdpsParser#funNameRef.
    def enterFunNameRef(self, ctx:OdpsParser.FunNameRefContext):
        pass

    # Exit a parse tree produced by OdpsParser#funNameRef.
    def exitFunNameRef(self, ctx:OdpsParser.FunNameRefContext):
        pass


    # Enter a parse tree produced by OdpsParser#lambdaExpression.
    def enterLambdaExpression(self, ctx:OdpsParser.LambdaExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#lambdaExpression.
    def exitLambdaExpression(self, ctx:OdpsParser.LambdaExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#lambdaParameter.
    def enterLambdaParameter(self, ctx:OdpsParser.LambdaParameterContext):
        pass

    # Exit a parse tree produced by OdpsParser#lambdaParameter.
    def exitLambdaParameter(self, ctx:OdpsParser.LambdaParameterContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableOrColumnRef.
    def enterTableOrColumnRef(self, ctx:OdpsParser.TableOrColumnRefContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableOrColumnRef.
    def exitTableOrColumnRef(self, ctx:OdpsParser.TableOrColumnRefContext):
        pass


    # Enter a parse tree produced by OdpsParser#newExpression.
    def enterNewExpression(self, ctx:OdpsParser.NewExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#newExpression.
    def exitNewExpression(self, ctx:OdpsParser.NewExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#existsExpression.
    def enterExistsExpression(self, ctx:OdpsParser.ExistsExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#existsExpression.
    def exitExistsExpression(self, ctx:OdpsParser.ExistsExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#scalarSubQueryExpression.
    def enterScalarSubQueryExpression(self, ctx:OdpsParser.ScalarSubQueryExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#scalarSubQueryExpression.
    def exitScalarSubQueryExpression(self, ctx:OdpsParser.ScalarSubQueryExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#classNameWithPackage.
    def enterClassNameWithPackage(self, ctx:OdpsParser.ClassNameWithPackageContext):
        pass

    # Exit a parse tree produced by OdpsParser#classNameWithPackage.
    def exitClassNameWithPackage(self, ctx:OdpsParser.ClassNameWithPackageContext):
        pass


    # Enter a parse tree produced by OdpsParser#classNameOrArrayDecl.
    def enterClassNameOrArrayDecl(self, ctx:OdpsParser.ClassNameOrArrayDeclContext):
        pass

    # Exit a parse tree produced by OdpsParser#classNameOrArrayDecl.
    def exitClassNameOrArrayDecl(self, ctx:OdpsParser.ClassNameOrArrayDeclContext):
        pass


    # Enter a parse tree produced by OdpsParser#classNameList.
    def enterClassNameList(self, ctx:OdpsParser.ClassNameListContext):
        pass

    # Exit a parse tree produced by OdpsParser#classNameList.
    def exitClassNameList(self, ctx:OdpsParser.ClassNameListContext):
        pass


    # Enter a parse tree produced by OdpsParser#odpsqlNonReserved.
    def enterOdpsqlNonReserved(self, ctx:OdpsParser.OdpsqlNonReservedContext):
        pass

    # Exit a parse tree produced by OdpsParser#odpsqlNonReserved.
    def exitOdpsqlNonReserved(self, ctx:OdpsParser.OdpsqlNonReservedContext):
        pass


    # Enter a parse tree produced by OdpsParser#relaxedKeywords.
    def enterRelaxedKeywords(self, ctx:OdpsParser.RelaxedKeywordsContext):
        pass

    # Exit a parse tree produced by OdpsParser#relaxedKeywords.
    def exitRelaxedKeywords(self, ctx:OdpsParser.RelaxedKeywordsContext):
        pass


    # Enter a parse tree produced by OdpsParser#allIdentifiers.
    def enterAllIdentifiers(self, ctx:OdpsParser.AllIdentifiersContext):
        pass

    # Exit a parse tree produced by OdpsParser#allIdentifiers.
    def exitAllIdentifiers(self, ctx:OdpsParser.AllIdentifiersContext):
        pass


    # Enter a parse tree produced by OdpsParser#identifier.
    def enterIdentifier(self, ctx:OdpsParser.IdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#identifier.
    def exitIdentifier(self, ctx:OdpsParser.IdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#aliasIdentifier.
    def enterAliasIdentifier(self, ctx:OdpsParser.AliasIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#aliasIdentifier.
    def exitAliasIdentifier(self, ctx:OdpsParser.AliasIdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#identifierWithoutSql11.
    def enterIdentifierWithoutSql11(self, ctx:OdpsParser.IdentifierWithoutSql11Context):
        pass

    # Exit a parse tree produced by OdpsParser#identifierWithoutSql11.
    def exitIdentifierWithoutSql11(self, ctx:OdpsParser.IdentifierWithoutSql11Context):
        pass


    # Enter a parse tree produced by OdpsParser#alterTableChangeOwner.
    def enterAlterTableChangeOwner(self, ctx:OdpsParser.AlterTableChangeOwnerContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterTableChangeOwner.
    def exitAlterTableChangeOwner(self, ctx:OdpsParser.AlterTableChangeOwnerContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterViewChangeOwner.
    def enterAlterViewChangeOwner(self, ctx:OdpsParser.AlterViewChangeOwnerContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterViewChangeOwner.
    def exitAlterViewChangeOwner(self, ctx:OdpsParser.AlterViewChangeOwnerContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterTableEnableHubTable.
    def enterAlterTableEnableHubTable(self, ctx:OdpsParser.AlterTableEnableHubTableContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterTableEnableHubTable.
    def exitAlterTableEnableHubTable(self, ctx:OdpsParser.AlterTableEnableHubTableContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableLifecycle.
    def enterTableLifecycle(self, ctx:OdpsParser.TableLifecycleContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableLifecycle.
    def exitTableLifecycle(self, ctx:OdpsParser.TableLifecycleContext):
        pass


    # Enter a parse tree produced by OdpsParser#setStatement.
    def enterSetStatement(self, ctx:OdpsParser.SetStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#setStatement.
    def exitSetStatement(self, ctx:OdpsParser.SetStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#anythingButEqualOrSemi.
    def enterAnythingButEqualOrSemi(self, ctx:OdpsParser.AnythingButEqualOrSemiContext):
        pass

    # Exit a parse tree produced by OdpsParser#anythingButEqualOrSemi.
    def exitAnythingButEqualOrSemi(self, ctx:OdpsParser.AnythingButEqualOrSemiContext):
        pass


    # Enter a parse tree produced by OdpsParser#anythingButSemi.
    def enterAnythingButSemi(self, ctx:OdpsParser.AnythingButSemiContext):
        pass

    # Exit a parse tree produced by OdpsParser#anythingButSemi.
    def exitAnythingButSemi(self, ctx:OdpsParser.AnythingButSemiContext):
        pass


    # Enter a parse tree produced by OdpsParser#setProjectStatement.
    def enterSetProjectStatement(self, ctx:OdpsParser.SetProjectStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#setProjectStatement.
    def exitSetProjectStatement(self, ctx:OdpsParser.SetProjectStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#label.
    def enterLabel(self, ctx:OdpsParser.LabelContext):
        pass

    # Exit a parse tree produced by OdpsParser#label.
    def exitLabel(self, ctx:OdpsParser.LabelContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewInfoVal.
    def enterSkewInfoVal(self, ctx:OdpsParser.SkewInfoValContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewInfoVal.
    def exitSkewInfoVal(self, ctx:OdpsParser.SkewInfoValContext):
        pass


    # Enter a parse tree produced by OdpsParser#memberAccessOperator.
    def enterMemberAccessOperator(self, ctx:OdpsParser.MemberAccessOperatorContext):
        pass

    # Exit a parse tree produced by OdpsParser#memberAccessOperator.
    def exitMemberAccessOperator(self, ctx:OdpsParser.MemberAccessOperatorContext):
        pass


    # Enter a parse tree produced by OdpsParser#methodAccessOperator.
    def enterMethodAccessOperator(self, ctx:OdpsParser.MethodAccessOperatorContext):
        pass

    # Exit a parse tree produced by OdpsParser#methodAccessOperator.
    def exitMethodAccessOperator(self, ctx:OdpsParser.MethodAccessOperatorContext):
        pass


    # Enter a parse tree produced by OdpsParser#isNullOperator.
    def enterIsNullOperator(self, ctx:OdpsParser.IsNullOperatorContext):
        pass

    # Exit a parse tree produced by OdpsParser#isNullOperator.
    def exitIsNullOperator(self, ctx:OdpsParser.IsNullOperatorContext):
        pass


    # Enter a parse tree produced by OdpsParser#inOperator.
    def enterInOperator(self, ctx:OdpsParser.InOperatorContext):
        pass

    # Exit a parse tree produced by OdpsParser#inOperator.
    def exitInOperator(self, ctx:OdpsParser.InOperatorContext):
        pass


    # Enter a parse tree produced by OdpsParser#betweenOperator.
    def enterBetweenOperator(self, ctx:OdpsParser.BetweenOperatorContext):
        pass

    # Exit a parse tree produced by OdpsParser#betweenOperator.
    def exitBetweenOperator(self, ctx:OdpsParser.BetweenOperatorContext):
        pass


    # Enter a parse tree produced by OdpsParser#mathExpression.
    def enterMathExpression(self, ctx:OdpsParser.MathExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#mathExpression.
    def exitMathExpression(self, ctx:OdpsParser.MathExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#unarySuffixExpression.
    def enterUnarySuffixExpression(self, ctx:OdpsParser.UnarySuffixExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#unarySuffixExpression.
    def exitUnarySuffixExpression(self, ctx:OdpsParser.UnarySuffixExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#unaryPrefixExpression.
    def enterUnaryPrefixExpression(self, ctx:OdpsParser.UnaryPrefixExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#unaryPrefixExpression.
    def exitUnaryPrefixExpression(self, ctx:OdpsParser.UnaryPrefixExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#fieldExpression.
    def enterFieldExpression(self, ctx:OdpsParser.FieldExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#fieldExpression.
    def exitFieldExpression(self, ctx:OdpsParser.FieldExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#logicalExpression.
    def enterLogicalExpression(self, ctx:OdpsParser.LogicalExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#logicalExpression.
    def exitLogicalExpression(self, ctx:OdpsParser.LogicalExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#notExpression.
    def enterNotExpression(self, ctx:OdpsParser.NotExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#notExpression.
    def exitNotExpression(self, ctx:OdpsParser.NotExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#equalExpression.
    def enterEqualExpression(self, ctx:OdpsParser.EqualExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#equalExpression.
    def exitEqualExpression(self, ctx:OdpsParser.EqualExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#mathExpressionListInParentheses.
    def enterMathExpressionListInParentheses(self, ctx:OdpsParser.MathExpressionListInParenthesesContext):
        pass

    # Exit a parse tree produced by OdpsParser#mathExpressionListInParentheses.
    def exitMathExpressionListInParentheses(self, ctx:OdpsParser.MathExpressionListInParenthesesContext):
        pass


    # Enter a parse tree produced by OdpsParser#mathExpressionList.
    def enterMathExpressionList(self, ctx:OdpsParser.MathExpressionListContext):
        pass

    # Exit a parse tree produced by OdpsParser#mathExpressionList.
    def exitMathExpressionList(self, ctx:OdpsParser.MathExpressionListContext):
        pass


    # Enter a parse tree produced by OdpsParser#expression.
    def enterExpression(self, ctx:OdpsParser.ExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#expression.
    def exitExpression(self, ctx:OdpsParser.ExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#statisticStatement.
    def enterStatisticStatement(self, ctx:OdpsParser.StatisticStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#statisticStatement.
    def exitStatisticStatement(self, ctx:OdpsParser.StatisticStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#addRemoveStatisticStatement.
    def enterAddRemoveStatisticStatement(self, ctx:OdpsParser.AddRemoveStatisticStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#addRemoveStatisticStatement.
    def exitAddRemoveStatisticStatement(self, ctx:OdpsParser.AddRemoveStatisticStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#statisticInfo.
    def enterStatisticInfo(self, ctx:OdpsParser.StatisticInfoContext):
        pass

    # Exit a parse tree produced by OdpsParser#statisticInfo.
    def exitStatisticInfo(self, ctx:OdpsParser.StatisticInfoContext):
        pass


    # Enter a parse tree produced by OdpsParser#showStatisticStatement.
    def enterShowStatisticStatement(self, ctx:OdpsParser.ShowStatisticStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#showStatisticStatement.
    def exitShowStatisticStatement(self, ctx:OdpsParser.ShowStatisticStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#showStatisticListStatement.
    def enterShowStatisticListStatement(self, ctx:OdpsParser.ShowStatisticListStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#showStatisticListStatement.
    def exitShowStatisticListStatement(self, ctx:OdpsParser.ShowStatisticListStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#countTableStatement.
    def enterCountTableStatement(self, ctx:OdpsParser.CountTableStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#countTableStatement.
    def exitCountTableStatement(self, ctx:OdpsParser.CountTableStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#statisticName.
    def enterStatisticName(self, ctx:OdpsParser.StatisticNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#statisticName.
    def exitStatisticName(self, ctx:OdpsParser.StatisticNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#instanceManagement.
    def enterInstanceManagement(self, ctx:OdpsParser.InstanceManagementContext):
        pass

    # Exit a parse tree produced by OdpsParser#instanceManagement.
    def exitInstanceManagement(self, ctx:OdpsParser.InstanceManagementContext):
        pass


    # Enter a parse tree produced by OdpsParser#instanceStatus.
    def enterInstanceStatus(self, ctx:OdpsParser.InstanceStatusContext):
        pass

    # Exit a parse tree produced by OdpsParser#instanceStatus.
    def exitInstanceStatus(self, ctx:OdpsParser.InstanceStatusContext):
        pass


    # Enter a parse tree produced by OdpsParser#killInstance.
    def enterKillInstance(self, ctx:OdpsParser.KillInstanceContext):
        pass

    # Exit a parse tree produced by OdpsParser#killInstance.
    def exitKillInstance(self, ctx:OdpsParser.KillInstanceContext):
        pass


    # Enter a parse tree produced by OdpsParser#instanceId.
    def enterInstanceId(self, ctx:OdpsParser.InstanceIdContext):
        pass

    # Exit a parse tree produced by OdpsParser#instanceId.
    def exitInstanceId(self, ctx:OdpsParser.InstanceIdContext):
        pass


    # Enter a parse tree produced by OdpsParser#resourceManagement.
    def enterResourceManagement(self, ctx:OdpsParser.ResourceManagementContext):
        pass

    # Exit a parse tree produced by OdpsParser#resourceManagement.
    def exitResourceManagement(self, ctx:OdpsParser.ResourceManagementContext):
        pass


    # Enter a parse tree produced by OdpsParser#addResource.
    def enterAddResource(self, ctx:OdpsParser.AddResourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#addResource.
    def exitAddResource(self, ctx:OdpsParser.AddResourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropResource.
    def enterDropResource(self, ctx:OdpsParser.DropResourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropResource.
    def exitDropResource(self, ctx:OdpsParser.DropResourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#resourceId.
    def enterResourceId(self, ctx:OdpsParser.ResourceIdContext):
        pass

    # Exit a parse tree produced by OdpsParser#resourceId.
    def exitResourceId(self, ctx:OdpsParser.ResourceIdContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropOfflineModel.
    def enterDropOfflineModel(self, ctx:OdpsParser.DropOfflineModelContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropOfflineModel.
    def exitDropOfflineModel(self, ctx:OdpsParser.DropOfflineModelContext):
        pass


    # Enter a parse tree produced by OdpsParser#getResource.
    def enterGetResource(self, ctx:OdpsParser.GetResourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#getResource.
    def exitGetResource(self, ctx:OdpsParser.GetResourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#options.
    def enterOptions(self, ctx:OdpsParser.OptionsContext):
        pass

    # Exit a parse tree produced by OdpsParser#options.
    def exitOptions(self, ctx:OdpsParser.OptionsContext):
        pass


    # Enter a parse tree produced by OdpsParser#authorizationStatement.
    def enterAuthorizationStatement(self, ctx:OdpsParser.AuthorizationStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#authorizationStatement.
    def exitAuthorizationStatement(self, ctx:OdpsParser.AuthorizationStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#listUsers.
    def enterListUsers(self, ctx:OdpsParser.ListUsersContext):
        pass

    # Exit a parse tree produced by OdpsParser#listUsers.
    def exitListUsers(self, ctx:OdpsParser.ListUsersContext):
        pass


    # Enter a parse tree produced by OdpsParser#listGroups.
    def enterListGroups(self, ctx:OdpsParser.ListGroupsContext):
        pass

    # Exit a parse tree produced by OdpsParser#listGroups.
    def exitListGroups(self, ctx:OdpsParser.ListGroupsContext):
        pass


    # Enter a parse tree produced by OdpsParser#addUserStatement.
    def enterAddUserStatement(self, ctx:OdpsParser.AddUserStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#addUserStatement.
    def exitAddUserStatement(self, ctx:OdpsParser.AddUserStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#addGroupStatement.
    def enterAddGroupStatement(self, ctx:OdpsParser.AddGroupStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#addGroupStatement.
    def exitAddGroupStatement(self, ctx:OdpsParser.AddGroupStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#removeUserStatement.
    def enterRemoveUserStatement(self, ctx:OdpsParser.RemoveUserStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#removeUserStatement.
    def exitRemoveUserStatement(self, ctx:OdpsParser.RemoveUserStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#removeGroupStatement.
    def enterRemoveGroupStatement(self, ctx:OdpsParser.RemoveGroupStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#removeGroupStatement.
    def exitRemoveGroupStatement(self, ctx:OdpsParser.RemoveGroupStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#addAccountProvider.
    def enterAddAccountProvider(self, ctx:OdpsParser.AddAccountProviderContext):
        pass

    # Exit a parse tree produced by OdpsParser#addAccountProvider.
    def exitAddAccountProvider(self, ctx:OdpsParser.AddAccountProviderContext):
        pass


    # Enter a parse tree produced by OdpsParser#removeAccountProvider.
    def enterRemoveAccountProvider(self, ctx:OdpsParser.RemoveAccountProviderContext):
        pass

    # Exit a parse tree produced by OdpsParser#removeAccountProvider.
    def exitRemoveAccountProvider(self, ctx:OdpsParser.RemoveAccountProviderContext):
        pass


    # Enter a parse tree produced by OdpsParser#showAcl.
    def enterShowAcl(self, ctx:OdpsParser.ShowAclContext):
        pass

    # Exit a parse tree produced by OdpsParser#showAcl.
    def exitShowAcl(self, ctx:OdpsParser.ShowAclContext):
        pass


    # Enter a parse tree produced by OdpsParser#listRoles.
    def enterListRoles(self, ctx:OdpsParser.ListRolesContext):
        pass

    # Exit a parse tree produced by OdpsParser#listRoles.
    def exitListRoles(self, ctx:OdpsParser.ListRolesContext):
        pass


    # Enter a parse tree produced by OdpsParser#whoami.
    def enterWhoami(self, ctx:OdpsParser.WhoamiContext):
        pass

    # Exit a parse tree produced by OdpsParser#whoami.
    def exitWhoami(self, ctx:OdpsParser.WhoamiContext):
        pass


    # Enter a parse tree produced by OdpsParser#listTrustedProjects.
    def enterListTrustedProjects(self, ctx:OdpsParser.ListTrustedProjectsContext):
        pass

    # Exit a parse tree produced by OdpsParser#listTrustedProjects.
    def exitListTrustedProjects(self, ctx:OdpsParser.ListTrustedProjectsContext):
        pass


    # Enter a parse tree produced by OdpsParser#addTrustedProject.
    def enterAddTrustedProject(self, ctx:OdpsParser.AddTrustedProjectContext):
        pass

    # Exit a parse tree produced by OdpsParser#addTrustedProject.
    def exitAddTrustedProject(self, ctx:OdpsParser.AddTrustedProjectContext):
        pass


    # Enter a parse tree produced by OdpsParser#removeTrustedProject.
    def enterRemoveTrustedProject(self, ctx:OdpsParser.RemoveTrustedProjectContext):
        pass

    # Exit a parse tree produced by OdpsParser#removeTrustedProject.
    def exitRemoveTrustedProject(self, ctx:OdpsParser.RemoveTrustedProjectContext):
        pass


    # Enter a parse tree produced by OdpsParser#showSecurityConfiguration.
    def enterShowSecurityConfiguration(self, ctx:OdpsParser.ShowSecurityConfigurationContext):
        pass

    # Exit a parse tree produced by OdpsParser#showSecurityConfiguration.
    def exitShowSecurityConfiguration(self, ctx:OdpsParser.ShowSecurityConfigurationContext):
        pass


    # Enter a parse tree produced by OdpsParser#showPackages.
    def enterShowPackages(self, ctx:OdpsParser.ShowPackagesContext):
        pass

    # Exit a parse tree produced by OdpsParser#showPackages.
    def exitShowPackages(self, ctx:OdpsParser.ShowPackagesContext):
        pass


    # Enter a parse tree produced by OdpsParser#showItems.
    def enterShowItems(self, ctx:OdpsParser.ShowItemsContext):
        pass

    # Exit a parse tree produced by OdpsParser#showItems.
    def exitShowItems(self, ctx:OdpsParser.ShowItemsContext):
        pass


    # Enter a parse tree produced by OdpsParser#installPackage.
    def enterInstallPackage(self, ctx:OdpsParser.InstallPackageContext):
        pass

    # Exit a parse tree produced by OdpsParser#installPackage.
    def exitInstallPackage(self, ctx:OdpsParser.InstallPackageContext):
        pass


    # Enter a parse tree produced by OdpsParser#uninstallPackage.
    def enterUninstallPackage(self, ctx:OdpsParser.UninstallPackageContext):
        pass

    # Exit a parse tree produced by OdpsParser#uninstallPackage.
    def exitUninstallPackage(self, ctx:OdpsParser.UninstallPackageContext):
        pass


    # Enter a parse tree produced by OdpsParser#createPackage.
    def enterCreatePackage(self, ctx:OdpsParser.CreatePackageContext):
        pass

    # Exit a parse tree produced by OdpsParser#createPackage.
    def exitCreatePackage(self, ctx:OdpsParser.CreatePackageContext):
        pass


    # Enter a parse tree produced by OdpsParser#deletePackage.
    def enterDeletePackage(self, ctx:OdpsParser.DeletePackageContext):
        pass

    # Exit a parse tree produced by OdpsParser#deletePackage.
    def exitDeletePackage(self, ctx:OdpsParser.DeletePackageContext):
        pass


    # Enter a parse tree produced by OdpsParser#addToPackage.
    def enterAddToPackage(self, ctx:OdpsParser.AddToPackageContext):
        pass

    # Exit a parse tree produced by OdpsParser#addToPackage.
    def exitAddToPackage(self, ctx:OdpsParser.AddToPackageContext):
        pass


    # Enter a parse tree produced by OdpsParser#removeFromPackage.
    def enterRemoveFromPackage(self, ctx:OdpsParser.RemoveFromPackageContext):
        pass

    # Exit a parse tree produced by OdpsParser#removeFromPackage.
    def exitRemoveFromPackage(self, ctx:OdpsParser.RemoveFromPackageContext):
        pass


    # Enter a parse tree produced by OdpsParser#allowPackage.
    def enterAllowPackage(self, ctx:OdpsParser.AllowPackageContext):
        pass

    # Exit a parse tree produced by OdpsParser#allowPackage.
    def exitAllowPackage(self, ctx:OdpsParser.AllowPackageContext):
        pass


    # Enter a parse tree produced by OdpsParser#disallowPackage.
    def enterDisallowPackage(self, ctx:OdpsParser.DisallowPackageContext):
        pass

    # Exit a parse tree produced by OdpsParser#disallowPackage.
    def exitDisallowPackage(self, ctx:OdpsParser.DisallowPackageContext):
        pass


    # Enter a parse tree produced by OdpsParser#putPolicy.
    def enterPutPolicy(self, ctx:OdpsParser.PutPolicyContext):
        pass

    # Exit a parse tree produced by OdpsParser#putPolicy.
    def exitPutPolicy(self, ctx:OdpsParser.PutPolicyContext):
        pass


    # Enter a parse tree produced by OdpsParser#getPolicy.
    def enterGetPolicy(self, ctx:OdpsParser.GetPolicyContext):
        pass

    # Exit a parse tree produced by OdpsParser#getPolicy.
    def exitGetPolicy(self, ctx:OdpsParser.GetPolicyContext):
        pass


    # Enter a parse tree produced by OdpsParser#clearExpiredGrants.
    def enterClearExpiredGrants(self, ctx:OdpsParser.ClearExpiredGrantsContext):
        pass

    # Exit a parse tree produced by OdpsParser#clearExpiredGrants.
    def exitClearExpiredGrants(self, ctx:OdpsParser.ClearExpiredGrantsContext):
        pass


    # Enter a parse tree produced by OdpsParser#grantLabel.
    def enterGrantLabel(self, ctx:OdpsParser.GrantLabelContext):
        pass

    # Exit a parse tree produced by OdpsParser#grantLabel.
    def exitGrantLabel(self, ctx:OdpsParser.GrantLabelContext):
        pass


    # Enter a parse tree produced by OdpsParser#revokeLabel.
    def enterRevokeLabel(self, ctx:OdpsParser.RevokeLabelContext):
        pass

    # Exit a parse tree produced by OdpsParser#revokeLabel.
    def exitRevokeLabel(self, ctx:OdpsParser.RevokeLabelContext):
        pass


    # Enter a parse tree produced by OdpsParser#showLabel.
    def enterShowLabel(self, ctx:OdpsParser.ShowLabelContext):
        pass

    # Exit a parse tree produced by OdpsParser#showLabel.
    def exitShowLabel(self, ctx:OdpsParser.ShowLabelContext):
        pass


    # Enter a parse tree produced by OdpsParser#grantSuperPrivilege.
    def enterGrantSuperPrivilege(self, ctx:OdpsParser.GrantSuperPrivilegeContext):
        pass

    # Exit a parse tree produced by OdpsParser#grantSuperPrivilege.
    def exitGrantSuperPrivilege(self, ctx:OdpsParser.GrantSuperPrivilegeContext):
        pass


    # Enter a parse tree produced by OdpsParser#revokeSuperPrivilege.
    def enterRevokeSuperPrivilege(self, ctx:OdpsParser.RevokeSuperPrivilegeContext):
        pass

    # Exit a parse tree produced by OdpsParser#revokeSuperPrivilege.
    def exitRevokeSuperPrivilege(self, ctx:OdpsParser.RevokeSuperPrivilegeContext):
        pass


    # Enter a parse tree produced by OdpsParser#createRoleStatement.
    def enterCreateRoleStatement(self, ctx:OdpsParser.CreateRoleStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#createRoleStatement.
    def exitCreateRoleStatement(self, ctx:OdpsParser.CreateRoleStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropRoleStatement.
    def enterDropRoleStatement(self, ctx:OdpsParser.DropRoleStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropRoleStatement.
    def exitDropRoleStatement(self, ctx:OdpsParser.DropRoleStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#addRoleToProject.
    def enterAddRoleToProject(self, ctx:OdpsParser.AddRoleToProjectContext):
        pass

    # Exit a parse tree produced by OdpsParser#addRoleToProject.
    def exitAddRoleToProject(self, ctx:OdpsParser.AddRoleToProjectContext):
        pass


    # Enter a parse tree produced by OdpsParser#removeRoleFromProject.
    def enterRemoveRoleFromProject(self, ctx:OdpsParser.RemoveRoleFromProjectContext):
        pass

    # Exit a parse tree produced by OdpsParser#removeRoleFromProject.
    def exitRemoveRoleFromProject(self, ctx:OdpsParser.RemoveRoleFromProjectContext):
        pass


    # Enter a parse tree produced by OdpsParser#grantRole.
    def enterGrantRole(self, ctx:OdpsParser.GrantRoleContext):
        pass

    # Exit a parse tree produced by OdpsParser#grantRole.
    def exitGrantRole(self, ctx:OdpsParser.GrantRoleContext):
        pass


    # Enter a parse tree produced by OdpsParser#revokeRole.
    def enterRevokeRole(self, ctx:OdpsParser.RevokeRoleContext):
        pass

    # Exit a parse tree produced by OdpsParser#revokeRole.
    def exitRevokeRole(self, ctx:OdpsParser.RevokeRoleContext):
        pass


    # Enter a parse tree produced by OdpsParser#grantPrivileges.
    def enterGrantPrivileges(self, ctx:OdpsParser.GrantPrivilegesContext):
        pass

    # Exit a parse tree produced by OdpsParser#grantPrivileges.
    def exitGrantPrivileges(self, ctx:OdpsParser.GrantPrivilegesContext):
        pass


    # Enter a parse tree produced by OdpsParser#privilegeProperties.
    def enterPrivilegeProperties(self, ctx:OdpsParser.PrivilegePropertiesContext):
        pass

    # Exit a parse tree produced by OdpsParser#privilegeProperties.
    def exitPrivilegeProperties(self, ctx:OdpsParser.PrivilegePropertiesContext):
        pass


    # Enter a parse tree produced by OdpsParser#privilegePropertieKeys.
    def enterPrivilegePropertieKeys(self, ctx:OdpsParser.PrivilegePropertieKeysContext):
        pass

    # Exit a parse tree produced by OdpsParser#privilegePropertieKeys.
    def exitPrivilegePropertieKeys(self, ctx:OdpsParser.PrivilegePropertieKeysContext):
        pass


    # Enter a parse tree produced by OdpsParser#revokePrivileges.
    def enterRevokePrivileges(self, ctx:OdpsParser.RevokePrivilegesContext):
        pass

    # Exit a parse tree produced by OdpsParser#revokePrivileges.
    def exitRevokePrivileges(self, ctx:OdpsParser.RevokePrivilegesContext):
        pass


    # Enter a parse tree produced by OdpsParser#purgePrivileges.
    def enterPurgePrivileges(self, ctx:OdpsParser.PurgePrivilegesContext):
        pass

    # Exit a parse tree produced by OdpsParser#purgePrivileges.
    def exitPurgePrivileges(self, ctx:OdpsParser.PurgePrivilegesContext):
        pass


    # Enter a parse tree produced by OdpsParser#showGrants.
    def enterShowGrants(self, ctx:OdpsParser.ShowGrantsContext):
        pass

    # Exit a parse tree produced by OdpsParser#showGrants.
    def exitShowGrants(self, ctx:OdpsParser.ShowGrantsContext):
        pass


    # Enter a parse tree produced by OdpsParser#showRoleGrants.
    def enterShowRoleGrants(self, ctx:OdpsParser.ShowRoleGrantsContext):
        pass

    # Exit a parse tree produced by OdpsParser#showRoleGrants.
    def exitShowRoleGrants(self, ctx:OdpsParser.ShowRoleGrantsContext):
        pass


    # Enter a parse tree produced by OdpsParser#showRoles.
    def enterShowRoles(self, ctx:OdpsParser.ShowRolesContext):
        pass

    # Exit a parse tree produced by OdpsParser#showRoles.
    def exitShowRoles(self, ctx:OdpsParser.ShowRolesContext):
        pass


    # Enter a parse tree produced by OdpsParser#showRolePrincipals.
    def enterShowRolePrincipals(self, ctx:OdpsParser.ShowRolePrincipalsContext):
        pass

    # Exit a parse tree produced by OdpsParser#showRolePrincipals.
    def exitShowRolePrincipals(self, ctx:OdpsParser.ShowRolePrincipalsContext):
        pass


    # Enter a parse tree produced by OdpsParser#user.
    def enterUser(self, ctx:OdpsParser.UserContext):
        pass

    # Exit a parse tree produced by OdpsParser#user.
    def exitUser(self, ctx:OdpsParser.UserContext):
        pass


    # Enter a parse tree produced by OdpsParser#userRoleComments.
    def enterUserRoleComments(self, ctx:OdpsParser.UserRoleCommentsContext):
        pass

    # Exit a parse tree produced by OdpsParser#userRoleComments.
    def exitUserRoleComments(self, ctx:OdpsParser.UserRoleCommentsContext):
        pass


    # Enter a parse tree produced by OdpsParser#accountProvider.
    def enterAccountProvider(self, ctx:OdpsParser.AccountProviderContext):
        pass

    # Exit a parse tree produced by OdpsParser#accountProvider.
    def exitAccountProvider(self, ctx:OdpsParser.AccountProviderContext):
        pass


    # Enter a parse tree produced by OdpsParser#projectName.
    def enterProjectName(self, ctx:OdpsParser.ProjectNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#projectName.
    def exitProjectName(self, ctx:OdpsParser.ProjectNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#privilegeObjectName.
    def enterPrivilegeObjectName(self, ctx:OdpsParser.PrivilegeObjectNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#privilegeObjectName.
    def exitPrivilegeObjectName(self, ctx:OdpsParser.PrivilegeObjectNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#privilegeObjectType.
    def enterPrivilegeObjectType(self, ctx:OdpsParser.PrivilegeObjectTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#privilegeObjectType.
    def exitPrivilegeObjectType(self, ctx:OdpsParser.PrivilegeObjectTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#roleName.
    def enterRoleName(self, ctx:OdpsParser.RoleNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#roleName.
    def exitRoleName(self, ctx:OdpsParser.RoleNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#packageName.
    def enterPackageName(self, ctx:OdpsParser.PackageNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#packageName.
    def exitPackageName(self, ctx:OdpsParser.PackageNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#packageNameWithProject.
    def enterPackageNameWithProject(self, ctx:OdpsParser.PackageNameWithProjectContext):
        pass

    # Exit a parse tree produced by OdpsParser#packageNameWithProject.
    def exitPackageNameWithProject(self, ctx:OdpsParser.PackageNameWithProjectContext):
        pass


    # Enter a parse tree produced by OdpsParser#principalSpecification.
    def enterPrincipalSpecification(self, ctx:OdpsParser.PrincipalSpecificationContext):
        pass

    # Exit a parse tree produced by OdpsParser#principalSpecification.
    def exitPrincipalSpecification(self, ctx:OdpsParser.PrincipalSpecificationContext):
        pass


    # Enter a parse tree produced by OdpsParser#principalName.
    def enterPrincipalName(self, ctx:OdpsParser.PrincipalNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#principalName.
    def exitPrincipalName(self, ctx:OdpsParser.PrincipalNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#principalIdentifier.
    def enterPrincipalIdentifier(self, ctx:OdpsParser.PrincipalIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#principalIdentifier.
    def exitPrincipalIdentifier(self, ctx:OdpsParser.PrincipalIdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#privilege.
    def enterPrivilege(self, ctx:OdpsParser.PrivilegeContext):
        pass

    # Exit a parse tree produced by OdpsParser#privilege.
    def exitPrivilege(self, ctx:OdpsParser.PrivilegeContext):
        pass


    # Enter a parse tree produced by OdpsParser#privilegeType.
    def enterPrivilegeType(self, ctx:OdpsParser.PrivilegeTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#privilegeType.
    def exitPrivilegeType(self, ctx:OdpsParser.PrivilegeTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#privilegeObject.
    def enterPrivilegeObject(self, ctx:OdpsParser.PrivilegeObjectContext):
        pass

    # Exit a parse tree produced by OdpsParser#privilegeObject.
    def exitPrivilegeObject(self, ctx:OdpsParser.PrivilegeObjectContext):
        pass


    # Enter a parse tree produced by OdpsParser#filePath.
    def enterFilePath(self, ctx:OdpsParser.FilePathContext):
        pass

    # Exit a parse tree produced by OdpsParser#filePath.
    def exitFilePath(self, ctx:OdpsParser.FilePathContext):
        pass


    # Enter a parse tree produced by OdpsParser#policyCondition.
    def enterPolicyCondition(self, ctx:OdpsParser.PolicyConditionContext):
        pass

    # Exit a parse tree produced by OdpsParser#policyCondition.
    def exitPolicyCondition(self, ctx:OdpsParser.PolicyConditionContext):
        pass


    # Enter a parse tree produced by OdpsParser#policyConditionOp.
    def enterPolicyConditionOp(self, ctx:OdpsParser.PolicyConditionOpContext):
        pass

    # Exit a parse tree produced by OdpsParser#policyConditionOp.
    def exitPolicyConditionOp(self, ctx:OdpsParser.PolicyConditionOpContext):
        pass


    # Enter a parse tree produced by OdpsParser#policyKey.
    def enterPolicyKey(self, ctx:OdpsParser.PolicyKeyContext):
        pass

    # Exit a parse tree produced by OdpsParser#policyKey.
    def exitPolicyKey(self, ctx:OdpsParser.PolicyKeyContext):
        pass


    # Enter a parse tree produced by OdpsParser#policyValue.
    def enterPolicyValue(self, ctx:OdpsParser.PolicyValueContext):
        pass

    # Exit a parse tree produced by OdpsParser#policyValue.
    def exitPolicyValue(self, ctx:OdpsParser.PolicyValueContext):
        pass


    # Enter a parse tree produced by OdpsParser#showCurrentRole.
    def enterShowCurrentRole(self, ctx:OdpsParser.ShowCurrentRoleContext):
        pass

    # Exit a parse tree produced by OdpsParser#showCurrentRole.
    def exitShowCurrentRole(self, ctx:OdpsParser.ShowCurrentRoleContext):
        pass


    # Enter a parse tree produced by OdpsParser#setRole.
    def enterSetRole(self, ctx:OdpsParser.SetRoleContext):
        pass

    # Exit a parse tree produced by OdpsParser#setRole.
    def exitSetRole(self, ctx:OdpsParser.SetRoleContext):
        pass


    # Enter a parse tree produced by OdpsParser#adminOptionFor.
    def enterAdminOptionFor(self, ctx:OdpsParser.AdminOptionForContext):
        pass

    # Exit a parse tree produced by OdpsParser#adminOptionFor.
    def exitAdminOptionFor(self, ctx:OdpsParser.AdminOptionForContext):
        pass


    # Enter a parse tree produced by OdpsParser#withAdminOption.
    def enterWithAdminOption(self, ctx:OdpsParser.WithAdminOptionContext):
        pass

    # Exit a parse tree produced by OdpsParser#withAdminOption.
    def exitWithAdminOption(self, ctx:OdpsParser.WithAdminOptionContext):
        pass


    # Enter a parse tree produced by OdpsParser#withGrantOption.
    def enterWithGrantOption(self, ctx:OdpsParser.WithGrantOptionContext):
        pass

    # Exit a parse tree produced by OdpsParser#withGrantOption.
    def exitWithGrantOption(self, ctx:OdpsParser.WithGrantOptionContext):
        pass


    # Enter a parse tree produced by OdpsParser#grantOptionFor.
    def enterGrantOptionFor(self, ctx:OdpsParser.GrantOptionForContext):
        pass

    # Exit a parse tree produced by OdpsParser#grantOptionFor.
    def exitGrantOptionFor(self, ctx:OdpsParser.GrantOptionForContext):
        pass


    # Enter a parse tree produced by OdpsParser#explainOption.
    def enterExplainOption(self, ctx:OdpsParser.ExplainOptionContext):
        pass

    # Exit a parse tree produced by OdpsParser#explainOption.
    def exitExplainOption(self, ctx:OdpsParser.ExplainOptionContext):
        pass


    # Enter a parse tree produced by OdpsParser#loadStatement.
    def enterLoadStatement(self, ctx:OdpsParser.LoadStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#loadStatement.
    def exitLoadStatement(self, ctx:OdpsParser.LoadStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#replicationClause.
    def enterReplicationClause(self, ctx:OdpsParser.ReplicationClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#replicationClause.
    def exitReplicationClause(self, ctx:OdpsParser.ReplicationClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#exportStatement.
    def enterExportStatement(self, ctx:OdpsParser.ExportStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#exportStatement.
    def exitExportStatement(self, ctx:OdpsParser.ExportStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#importStatement.
    def enterImportStatement(self, ctx:OdpsParser.ImportStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#importStatement.
    def exitImportStatement(self, ctx:OdpsParser.ImportStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#readStatement.
    def enterReadStatement(self, ctx:OdpsParser.ReadStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#readStatement.
    def exitReadStatement(self, ctx:OdpsParser.ReadStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#undoStatement.
    def enterUndoStatement(self, ctx:OdpsParser.UndoStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#undoStatement.
    def exitUndoStatement(self, ctx:OdpsParser.UndoStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#redoStatement.
    def enterRedoStatement(self, ctx:OdpsParser.RedoStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#redoStatement.
    def exitRedoStatement(self, ctx:OdpsParser.RedoStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#purgeStatement.
    def enterPurgeStatement(self, ctx:OdpsParser.PurgeStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#purgeStatement.
    def exitPurgeStatement(self, ctx:OdpsParser.PurgeStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropTableVairableStatement.
    def enterDropTableVairableStatement(self, ctx:OdpsParser.DropTableVairableStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropTableVairableStatement.
    def exitDropTableVairableStatement(self, ctx:OdpsParser.DropTableVairableStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#msckRepairTableStatement.
    def enterMsckRepairTableStatement(self, ctx:OdpsParser.MsckRepairTableStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#msckRepairTableStatement.
    def exitMsckRepairTableStatement(self, ctx:OdpsParser.MsckRepairTableStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#ddlStatement.
    def enterDdlStatement(self, ctx:OdpsParser.DdlStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#ddlStatement.
    def exitDdlStatement(self, ctx:OdpsParser.DdlStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#partitionSpecOrPartitionId.
    def enterPartitionSpecOrPartitionId(self, ctx:OdpsParser.PartitionSpecOrPartitionIdContext):
        pass

    # Exit a parse tree produced by OdpsParser#partitionSpecOrPartitionId.
    def exitPartitionSpecOrPartitionId(self, ctx:OdpsParser.PartitionSpecOrPartitionIdContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableOrTableId.
    def enterTableOrTableId(self, ctx:OdpsParser.TableOrTableIdContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableOrTableId.
    def exitTableOrTableId(self, ctx:OdpsParser.TableOrTableIdContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableHistoryStatement.
    def enterTableHistoryStatement(self, ctx:OdpsParser.TableHistoryStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableHistoryStatement.
    def exitTableHistoryStatement(self, ctx:OdpsParser.TableHistoryStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#setExstore.
    def enterSetExstore(self, ctx:OdpsParser.SetExstoreContext):
        pass

    # Exit a parse tree produced by OdpsParser#setExstore.
    def exitSetExstore(self, ctx:OdpsParser.SetExstoreContext):
        pass


    # Enter a parse tree produced by OdpsParser#ifExists.
    def enterIfExists(self, ctx:OdpsParser.IfExistsContext):
        pass

    # Exit a parse tree produced by OdpsParser#ifExists.
    def exitIfExists(self, ctx:OdpsParser.IfExistsContext):
        pass


    # Enter a parse tree produced by OdpsParser#restrictOrCascade.
    def enterRestrictOrCascade(self, ctx:OdpsParser.RestrictOrCascadeContext):
        pass

    # Exit a parse tree produced by OdpsParser#restrictOrCascade.
    def exitRestrictOrCascade(self, ctx:OdpsParser.RestrictOrCascadeContext):
        pass


    # Enter a parse tree produced by OdpsParser#ifNotExists.
    def enterIfNotExists(self, ctx:OdpsParser.IfNotExistsContext):
        pass

    # Exit a parse tree produced by OdpsParser#ifNotExists.
    def exitIfNotExists(self, ctx:OdpsParser.IfNotExistsContext):
        pass


    # Enter a parse tree produced by OdpsParser#rewriteEnabled.
    def enterRewriteEnabled(self, ctx:OdpsParser.RewriteEnabledContext):
        pass

    # Exit a parse tree produced by OdpsParser#rewriteEnabled.
    def exitRewriteEnabled(self, ctx:OdpsParser.RewriteEnabledContext):
        pass


    # Enter a parse tree produced by OdpsParser#rewriteDisabled.
    def enterRewriteDisabled(self, ctx:OdpsParser.RewriteDisabledContext):
        pass

    # Exit a parse tree produced by OdpsParser#rewriteDisabled.
    def exitRewriteDisabled(self, ctx:OdpsParser.RewriteDisabledContext):
        pass


    # Enter a parse tree produced by OdpsParser#storedAsDirs.
    def enterStoredAsDirs(self, ctx:OdpsParser.StoredAsDirsContext):
        pass

    # Exit a parse tree produced by OdpsParser#storedAsDirs.
    def exitStoredAsDirs(self, ctx:OdpsParser.StoredAsDirsContext):
        pass


    # Enter a parse tree produced by OdpsParser#orReplace.
    def enterOrReplace(self, ctx:OdpsParser.OrReplaceContext):
        pass

    # Exit a parse tree produced by OdpsParser#orReplace.
    def exitOrReplace(self, ctx:OdpsParser.OrReplaceContext):
        pass


    # Enter a parse tree produced by OdpsParser#ignoreProtection.
    def enterIgnoreProtection(self, ctx:OdpsParser.IgnoreProtectionContext):
        pass

    # Exit a parse tree produced by OdpsParser#ignoreProtection.
    def exitIgnoreProtection(self, ctx:OdpsParser.IgnoreProtectionContext):
        pass


    # Enter a parse tree produced by OdpsParser#createDatabaseStatement.
    def enterCreateDatabaseStatement(self, ctx:OdpsParser.CreateDatabaseStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#createDatabaseStatement.
    def exitCreateDatabaseStatement(self, ctx:OdpsParser.CreateDatabaseStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#schemaName.
    def enterSchemaName(self, ctx:OdpsParser.SchemaNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#schemaName.
    def exitSchemaName(self, ctx:OdpsParser.SchemaNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#createSchemaStatement.
    def enterCreateSchemaStatement(self, ctx:OdpsParser.CreateSchemaStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#createSchemaStatement.
    def exitCreateSchemaStatement(self, ctx:OdpsParser.CreateSchemaStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#dbLocation.
    def enterDbLocation(self, ctx:OdpsParser.DbLocationContext):
        pass

    # Exit a parse tree produced by OdpsParser#dbLocation.
    def exitDbLocation(self, ctx:OdpsParser.DbLocationContext):
        pass


    # Enter a parse tree produced by OdpsParser#dbProperties.
    def enterDbProperties(self, ctx:OdpsParser.DbPropertiesContext):
        pass

    # Exit a parse tree produced by OdpsParser#dbProperties.
    def exitDbProperties(self, ctx:OdpsParser.DbPropertiesContext):
        pass


    # Enter a parse tree produced by OdpsParser#dbPropertiesList.
    def enterDbPropertiesList(self, ctx:OdpsParser.DbPropertiesListContext):
        pass

    # Exit a parse tree produced by OdpsParser#dbPropertiesList.
    def exitDbPropertiesList(self, ctx:OdpsParser.DbPropertiesListContext):
        pass


    # Enter a parse tree produced by OdpsParser#switchDatabaseStatement.
    def enterSwitchDatabaseStatement(self, ctx:OdpsParser.SwitchDatabaseStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#switchDatabaseStatement.
    def exitSwitchDatabaseStatement(self, ctx:OdpsParser.SwitchDatabaseStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropDatabaseStatement.
    def enterDropDatabaseStatement(self, ctx:OdpsParser.DropDatabaseStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropDatabaseStatement.
    def exitDropDatabaseStatement(self, ctx:OdpsParser.DropDatabaseStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropSchemaStatement.
    def enterDropSchemaStatement(self, ctx:OdpsParser.DropSchemaStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropSchemaStatement.
    def exitDropSchemaStatement(self, ctx:OdpsParser.DropSchemaStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#databaseComment.
    def enterDatabaseComment(self, ctx:OdpsParser.DatabaseCommentContext):
        pass

    # Exit a parse tree produced by OdpsParser#databaseComment.
    def exitDatabaseComment(self, ctx:OdpsParser.DatabaseCommentContext):
        pass


    # Enter a parse tree produced by OdpsParser#dataFormatDesc.
    def enterDataFormatDesc(self, ctx:OdpsParser.DataFormatDescContext):
        pass

    # Exit a parse tree produced by OdpsParser#dataFormatDesc.
    def exitDataFormatDesc(self, ctx:OdpsParser.DataFormatDescContext):
        pass


    # Enter a parse tree produced by OdpsParser#createTableStatement.
    def enterCreateTableStatement(self, ctx:OdpsParser.CreateTableStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#createTableStatement.
    def exitCreateTableStatement(self, ctx:OdpsParser.CreateTableStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#truncateTableStatement.
    def enterTruncateTableStatement(self, ctx:OdpsParser.TruncateTableStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#truncateTableStatement.
    def exitTruncateTableStatement(self, ctx:OdpsParser.TruncateTableStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#createIndexStatement.
    def enterCreateIndexStatement(self, ctx:OdpsParser.CreateIndexStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#createIndexStatement.
    def exitCreateIndexStatement(self, ctx:OdpsParser.CreateIndexStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#indexComment.
    def enterIndexComment(self, ctx:OdpsParser.IndexCommentContext):
        pass

    # Exit a parse tree produced by OdpsParser#indexComment.
    def exitIndexComment(self, ctx:OdpsParser.IndexCommentContext):
        pass


    # Enter a parse tree produced by OdpsParser#autoRebuild.
    def enterAutoRebuild(self, ctx:OdpsParser.AutoRebuildContext):
        pass

    # Exit a parse tree produced by OdpsParser#autoRebuild.
    def exitAutoRebuild(self, ctx:OdpsParser.AutoRebuildContext):
        pass


    # Enter a parse tree produced by OdpsParser#indexTblName.
    def enterIndexTblName(self, ctx:OdpsParser.IndexTblNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#indexTblName.
    def exitIndexTblName(self, ctx:OdpsParser.IndexTblNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#indexPropertiesPrefixed.
    def enterIndexPropertiesPrefixed(self, ctx:OdpsParser.IndexPropertiesPrefixedContext):
        pass

    # Exit a parse tree produced by OdpsParser#indexPropertiesPrefixed.
    def exitIndexPropertiesPrefixed(self, ctx:OdpsParser.IndexPropertiesPrefixedContext):
        pass


    # Enter a parse tree produced by OdpsParser#indexProperties.
    def enterIndexProperties(self, ctx:OdpsParser.IndexPropertiesContext):
        pass

    # Exit a parse tree produced by OdpsParser#indexProperties.
    def exitIndexProperties(self, ctx:OdpsParser.IndexPropertiesContext):
        pass


    # Enter a parse tree produced by OdpsParser#indexPropertiesList.
    def enterIndexPropertiesList(self, ctx:OdpsParser.IndexPropertiesListContext):
        pass

    # Exit a parse tree produced by OdpsParser#indexPropertiesList.
    def exitIndexPropertiesList(self, ctx:OdpsParser.IndexPropertiesListContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropIndexStatement.
    def enterDropIndexStatement(self, ctx:OdpsParser.DropIndexStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropIndexStatement.
    def exitDropIndexStatement(self, ctx:OdpsParser.DropIndexStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropTableStatement.
    def enterDropTableStatement(self, ctx:OdpsParser.DropTableStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropTableStatement.
    def exitDropTableStatement(self, ctx:OdpsParser.DropTableStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatement.
    def enterAlterStatement(self, ctx:OdpsParser.AlterStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatement.
    def exitAlterStatement(self, ctx:OdpsParser.AlterStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterSchemaStatementSuffix.
    def enterAlterSchemaStatementSuffix(self, ctx:OdpsParser.AlterSchemaStatementSuffixContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterSchemaStatementSuffix.
    def exitAlterSchemaStatementSuffix(self, ctx:OdpsParser.AlterSchemaStatementSuffixContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterTableStatementSuffix.
    def enterAlterTableStatementSuffix(self, ctx:OdpsParser.AlterTableStatementSuffixContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterTableStatementSuffix.
    def exitAlterTableStatementSuffix(self, ctx:OdpsParser.AlterTableStatementSuffixContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterTableMergePartitionSuffix.
    def enterAlterTableMergePartitionSuffix(self, ctx:OdpsParser.AlterTableMergePartitionSuffixContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterTableMergePartitionSuffix.
    def exitAlterTableMergePartitionSuffix(self, ctx:OdpsParser.AlterTableMergePartitionSuffixContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixAddConstraint.
    def enterAlterStatementSuffixAddConstraint(self, ctx:OdpsParser.AlterStatementSuffixAddConstraintContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixAddConstraint.
    def exitAlterStatementSuffixAddConstraint(self, ctx:OdpsParser.AlterStatementSuffixAddConstraintContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterTblPartitionStatementSuffix.
    def enterAlterTblPartitionStatementSuffix(self, ctx:OdpsParser.AlterTblPartitionStatementSuffixContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterTblPartitionStatementSuffix.
    def exitAlterTblPartitionStatementSuffix(self, ctx:OdpsParser.AlterTblPartitionStatementSuffixContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixPartitionLifecycle.
    def enterAlterStatementSuffixPartitionLifecycle(self, ctx:OdpsParser.AlterStatementSuffixPartitionLifecycleContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixPartitionLifecycle.
    def exitAlterStatementSuffixPartitionLifecycle(self, ctx:OdpsParser.AlterStatementSuffixPartitionLifecycleContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterTblPartitionStatementSuffixProperties.
    def enterAlterTblPartitionStatementSuffixProperties(self, ctx:OdpsParser.AlterTblPartitionStatementSuffixPropertiesContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterTblPartitionStatementSuffixProperties.
    def exitAlterTblPartitionStatementSuffixProperties(self, ctx:OdpsParser.AlterTblPartitionStatementSuffixPropertiesContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementPartitionKeyType.
    def enterAlterStatementPartitionKeyType(self, ctx:OdpsParser.AlterStatementPartitionKeyTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementPartitionKeyType.
    def exitAlterStatementPartitionKeyType(self, ctx:OdpsParser.AlterStatementPartitionKeyTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterViewStatementSuffix.
    def enterAlterViewStatementSuffix(self, ctx:OdpsParser.AlterViewStatementSuffixContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterViewStatementSuffix.
    def exitAlterViewStatementSuffix(self, ctx:OdpsParser.AlterViewStatementSuffixContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterMaterializedViewStatementSuffix.
    def enterAlterMaterializedViewStatementSuffix(self, ctx:OdpsParser.AlterMaterializedViewStatementSuffixContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterMaterializedViewStatementSuffix.
    def exitAlterMaterializedViewStatementSuffix(self, ctx:OdpsParser.AlterMaterializedViewStatementSuffixContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterMaterializedViewSuffixRewrite.
    def enterAlterMaterializedViewSuffixRewrite(self, ctx:OdpsParser.AlterMaterializedViewSuffixRewriteContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterMaterializedViewSuffixRewrite.
    def exitAlterMaterializedViewSuffixRewrite(self, ctx:OdpsParser.AlterMaterializedViewSuffixRewriteContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterMaterializedViewSuffixRebuild.
    def enterAlterMaterializedViewSuffixRebuild(self, ctx:OdpsParser.AlterMaterializedViewSuffixRebuildContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterMaterializedViewSuffixRebuild.
    def exitAlterMaterializedViewSuffixRebuild(self, ctx:OdpsParser.AlterMaterializedViewSuffixRebuildContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterIndexStatementSuffix.
    def enterAlterIndexStatementSuffix(self, ctx:OdpsParser.AlterIndexStatementSuffixContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterIndexStatementSuffix.
    def exitAlterIndexStatementSuffix(self, ctx:OdpsParser.AlterIndexStatementSuffixContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterDatabaseStatementSuffix.
    def enterAlterDatabaseStatementSuffix(self, ctx:OdpsParser.AlterDatabaseStatementSuffixContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterDatabaseStatementSuffix.
    def exitAlterDatabaseStatementSuffix(self, ctx:OdpsParser.AlterDatabaseStatementSuffixContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterDatabaseSuffixProperties.
    def enterAlterDatabaseSuffixProperties(self, ctx:OdpsParser.AlterDatabaseSuffixPropertiesContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterDatabaseSuffixProperties.
    def exitAlterDatabaseSuffixProperties(self, ctx:OdpsParser.AlterDatabaseSuffixPropertiesContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterDatabaseSuffixSetOwner.
    def enterAlterDatabaseSuffixSetOwner(self, ctx:OdpsParser.AlterDatabaseSuffixSetOwnerContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterDatabaseSuffixSetOwner.
    def exitAlterDatabaseSuffixSetOwner(self, ctx:OdpsParser.AlterDatabaseSuffixSetOwnerContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixRename.
    def enterAlterStatementSuffixRename(self, ctx:OdpsParser.AlterStatementSuffixRenameContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixRename.
    def exitAlterStatementSuffixRename(self, ctx:OdpsParser.AlterStatementSuffixRenameContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixAddCol.
    def enterAlterStatementSuffixAddCol(self, ctx:OdpsParser.AlterStatementSuffixAddColContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixAddCol.
    def exitAlterStatementSuffixAddCol(self, ctx:OdpsParser.AlterStatementSuffixAddColContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixRenameCol.
    def enterAlterStatementSuffixRenameCol(self, ctx:OdpsParser.AlterStatementSuffixRenameColContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixRenameCol.
    def exitAlterStatementSuffixRenameCol(self, ctx:OdpsParser.AlterStatementSuffixRenameColContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixDropCol.
    def enterAlterStatementSuffixDropCol(self, ctx:OdpsParser.AlterStatementSuffixDropColContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixDropCol.
    def exitAlterStatementSuffixDropCol(self, ctx:OdpsParser.AlterStatementSuffixDropColContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixUpdateStatsCol.
    def enterAlterStatementSuffixUpdateStatsCol(self, ctx:OdpsParser.AlterStatementSuffixUpdateStatsColContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixUpdateStatsCol.
    def exitAlterStatementSuffixUpdateStatsCol(self, ctx:OdpsParser.AlterStatementSuffixUpdateStatsColContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementChangeColPosition.
    def enterAlterStatementChangeColPosition(self, ctx:OdpsParser.AlterStatementChangeColPositionContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementChangeColPosition.
    def exitAlterStatementChangeColPosition(self, ctx:OdpsParser.AlterStatementChangeColPositionContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixAddPartitions.
    def enterAlterStatementSuffixAddPartitions(self, ctx:OdpsParser.AlterStatementSuffixAddPartitionsContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixAddPartitions.
    def exitAlterStatementSuffixAddPartitions(self, ctx:OdpsParser.AlterStatementSuffixAddPartitionsContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixAddPartitionsElement.
    def enterAlterStatementSuffixAddPartitionsElement(self, ctx:OdpsParser.AlterStatementSuffixAddPartitionsElementContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixAddPartitionsElement.
    def exitAlterStatementSuffixAddPartitionsElement(self, ctx:OdpsParser.AlterStatementSuffixAddPartitionsElementContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixTouch.
    def enterAlterStatementSuffixTouch(self, ctx:OdpsParser.AlterStatementSuffixTouchContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixTouch.
    def exitAlterStatementSuffixTouch(self, ctx:OdpsParser.AlterStatementSuffixTouchContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixArchive.
    def enterAlterStatementSuffixArchive(self, ctx:OdpsParser.AlterStatementSuffixArchiveContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixArchive.
    def exitAlterStatementSuffixArchive(self, ctx:OdpsParser.AlterStatementSuffixArchiveContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixUnArchive.
    def enterAlterStatementSuffixUnArchive(self, ctx:OdpsParser.AlterStatementSuffixUnArchiveContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixUnArchive.
    def exitAlterStatementSuffixUnArchive(self, ctx:OdpsParser.AlterStatementSuffixUnArchiveContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixChangeOwner.
    def enterAlterStatementSuffixChangeOwner(self, ctx:OdpsParser.AlterStatementSuffixChangeOwnerContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixChangeOwner.
    def exitAlterStatementSuffixChangeOwner(self, ctx:OdpsParser.AlterStatementSuffixChangeOwnerContext):
        pass


    # Enter a parse tree produced by OdpsParser#partitionLocation.
    def enterPartitionLocation(self, ctx:OdpsParser.PartitionLocationContext):
        pass

    # Exit a parse tree produced by OdpsParser#partitionLocation.
    def exitPartitionLocation(self, ctx:OdpsParser.PartitionLocationContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixDropPartitions.
    def enterAlterStatementSuffixDropPartitions(self, ctx:OdpsParser.AlterStatementSuffixDropPartitionsContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixDropPartitions.
    def exitAlterStatementSuffixDropPartitions(self, ctx:OdpsParser.AlterStatementSuffixDropPartitionsContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixProperties.
    def enterAlterStatementSuffixProperties(self, ctx:OdpsParser.AlterStatementSuffixPropertiesContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixProperties.
    def exitAlterStatementSuffixProperties(self, ctx:OdpsParser.AlterStatementSuffixPropertiesContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterViewSuffixProperties.
    def enterAlterViewSuffixProperties(self, ctx:OdpsParser.AlterViewSuffixPropertiesContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterViewSuffixProperties.
    def exitAlterViewSuffixProperties(self, ctx:OdpsParser.AlterViewSuffixPropertiesContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterViewColumnCommentSuffix.
    def enterAlterViewColumnCommentSuffix(self, ctx:OdpsParser.AlterViewColumnCommentSuffixContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterViewColumnCommentSuffix.
    def exitAlterViewColumnCommentSuffix(self, ctx:OdpsParser.AlterViewColumnCommentSuffixContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixSerdeProperties.
    def enterAlterStatementSuffixSerdeProperties(self, ctx:OdpsParser.AlterStatementSuffixSerdePropertiesContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixSerdeProperties.
    def exitAlterStatementSuffixSerdeProperties(self, ctx:OdpsParser.AlterStatementSuffixSerdePropertiesContext):
        pass


    # Enter a parse tree produced by OdpsParser#tablePartitionPrefix.
    def enterTablePartitionPrefix(self, ctx:OdpsParser.TablePartitionPrefixContext):
        pass

    # Exit a parse tree produced by OdpsParser#tablePartitionPrefix.
    def exitTablePartitionPrefix(self, ctx:OdpsParser.TablePartitionPrefixContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixFileFormat.
    def enterAlterStatementSuffixFileFormat(self, ctx:OdpsParser.AlterStatementSuffixFileFormatContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixFileFormat.
    def exitAlterStatementSuffixFileFormat(self, ctx:OdpsParser.AlterStatementSuffixFileFormatContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixClusterbySortby.
    def enterAlterStatementSuffixClusterbySortby(self, ctx:OdpsParser.AlterStatementSuffixClusterbySortbyContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixClusterbySortby.
    def exitAlterStatementSuffixClusterbySortby(self, ctx:OdpsParser.AlterStatementSuffixClusterbySortbyContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterTblPartitionStatementSuffixSkewedLocation.
    def enterAlterTblPartitionStatementSuffixSkewedLocation(self, ctx:OdpsParser.AlterTblPartitionStatementSuffixSkewedLocationContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterTblPartitionStatementSuffixSkewedLocation.
    def exitAlterTblPartitionStatementSuffixSkewedLocation(self, ctx:OdpsParser.AlterTblPartitionStatementSuffixSkewedLocationContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewedLocations.
    def enterSkewedLocations(self, ctx:OdpsParser.SkewedLocationsContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewedLocations.
    def exitSkewedLocations(self, ctx:OdpsParser.SkewedLocationsContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewedLocationsList.
    def enterSkewedLocationsList(self, ctx:OdpsParser.SkewedLocationsListContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewedLocationsList.
    def exitSkewedLocationsList(self, ctx:OdpsParser.SkewedLocationsListContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewedLocationMap.
    def enterSkewedLocationMap(self, ctx:OdpsParser.SkewedLocationMapContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewedLocationMap.
    def exitSkewedLocationMap(self, ctx:OdpsParser.SkewedLocationMapContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixLocation.
    def enterAlterStatementSuffixLocation(self, ctx:OdpsParser.AlterStatementSuffixLocationContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixLocation.
    def exitAlterStatementSuffixLocation(self, ctx:OdpsParser.AlterStatementSuffixLocationContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixSkewedby.
    def enterAlterStatementSuffixSkewedby(self, ctx:OdpsParser.AlterStatementSuffixSkewedbyContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixSkewedby.
    def exitAlterStatementSuffixSkewedby(self, ctx:OdpsParser.AlterStatementSuffixSkewedbyContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixExchangePartition.
    def enterAlterStatementSuffixExchangePartition(self, ctx:OdpsParser.AlterStatementSuffixExchangePartitionContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixExchangePartition.
    def exitAlterStatementSuffixExchangePartition(self, ctx:OdpsParser.AlterStatementSuffixExchangePartitionContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixProtectMode.
    def enterAlterStatementSuffixProtectMode(self, ctx:OdpsParser.AlterStatementSuffixProtectModeContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixProtectMode.
    def exitAlterStatementSuffixProtectMode(self, ctx:OdpsParser.AlterStatementSuffixProtectModeContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixRenamePart.
    def enterAlterStatementSuffixRenamePart(self, ctx:OdpsParser.AlterStatementSuffixRenamePartContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixRenamePart.
    def exitAlterStatementSuffixRenamePart(self, ctx:OdpsParser.AlterStatementSuffixRenamePartContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixStatsPart.
    def enterAlterStatementSuffixStatsPart(self, ctx:OdpsParser.AlterStatementSuffixStatsPartContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixStatsPart.
    def exitAlterStatementSuffixStatsPart(self, ctx:OdpsParser.AlterStatementSuffixStatsPartContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixMergeFiles.
    def enterAlterStatementSuffixMergeFiles(self, ctx:OdpsParser.AlterStatementSuffixMergeFilesContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixMergeFiles.
    def exitAlterStatementSuffixMergeFiles(self, ctx:OdpsParser.AlterStatementSuffixMergeFilesContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterProtectMode.
    def enterAlterProtectMode(self, ctx:OdpsParser.AlterProtectModeContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterProtectMode.
    def exitAlterProtectMode(self, ctx:OdpsParser.AlterProtectModeContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterProtectModeMode.
    def enterAlterProtectModeMode(self, ctx:OdpsParser.AlterProtectModeModeContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterProtectModeMode.
    def exitAlterProtectModeMode(self, ctx:OdpsParser.AlterProtectModeModeContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixBucketNum.
    def enterAlterStatementSuffixBucketNum(self, ctx:OdpsParser.AlterStatementSuffixBucketNumContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixBucketNum.
    def exitAlterStatementSuffixBucketNum(self, ctx:OdpsParser.AlterStatementSuffixBucketNumContext):
        pass


    # Enter a parse tree produced by OdpsParser#alterStatementSuffixCompact.
    def enterAlterStatementSuffixCompact(self, ctx:OdpsParser.AlterStatementSuffixCompactContext):
        pass

    # Exit a parse tree produced by OdpsParser#alterStatementSuffixCompact.
    def exitAlterStatementSuffixCompact(self, ctx:OdpsParser.AlterStatementSuffixCompactContext):
        pass


    # Enter a parse tree produced by OdpsParser#fileFormat.
    def enterFileFormat(self, ctx:OdpsParser.FileFormatContext):
        pass

    # Exit a parse tree produced by OdpsParser#fileFormat.
    def exitFileFormat(self, ctx:OdpsParser.FileFormatContext):
        pass


    # Enter a parse tree produced by OdpsParser#tabTypeExpr.
    def enterTabTypeExpr(self, ctx:OdpsParser.TabTypeExprContext):
        pass

    # Exit a parse tree produced by OdpsParser#tabTypeExpr.
    def exitTabTypeExpr(self, ctx:OdpsParser.TabTypeExprContext):
        pass


    # Enter a parse tree produced by OdpsParser#partTypeExpr.
    def enterPartTypeExpr(self, ctx:OdpsParser.PartTypeExprContext):
        pass

    # Exit a parse tree produced by OdpsParser#partTypeExpr.
    def exitPartTypeExpr(self, ctx:OdpsParser.PartTypeExprContext):
        pass


    # Enter a parse tree produced by OdpsParser#descStatement.
    def enterDescStatement(self, ctx:OdpsParser.DescStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#descStatement.
    def exitDescStatement(self, ctx:OdpsParser.DescStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#analyzeStatement.
    def enterAnalyzeStatement(self, ctx:OdpsParser.AnalyzeStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#analyzeStatement.
    def exitAnalyzeStatement(self, ctx:OdpsParser.AnalyzeStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#forColumnsStatement.
    def enterForColumnsStatement(self, ctx:OdpsParser.ForColumnsStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#forColumnsStatement.
    def exitForColumnsStatement(self, ctx:OdpsParser.ForColumnsStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameOrList.
    def enterColumnNameOrList(self, ctx:OdpsParser.ColumnNameOrListContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameOrList.
    def exitColumnNameOrList(self, ctx:OdpsParser.ColumnNameOrListContext):
        pass


    # Enter a parse tree produced by OdpsParser#showStatement.
    def enterShowStatement(self, ctx:OdpsParser.ShowStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#showStatement.
    def exitShowStatement(self, ctx:OdpsParser.ShowStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#listStatement.
    def enterListStatement(self, ctx:OdpsParser.ListStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#listStatement.
    def exitListStatement(self, ctx:OdpsParser.ListStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#bareDate.
    def enterBareDate(self, ctx:OdpsParser.BareDateContext):
        pass

    # Exit a parse tree produced by OdpsParser#bareDate.
    def exitBareDate(self, ctx:OdpsParser.BareDateContext):
        pass


    # Enter a parse tree produced by OdpsParser#lockStatement.
    def enterLockStatement(self, ctx:OdpsParser.LockStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#lockStatement.
    def exitLockStatement(self, ctx:OdpsParser.LockStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#lockDatabase.
    def enterLockDatabase(self, ctx:OdpsParser.LockDatabaseContext):
        pass

    # Exit a parse tree produced by OdpsParser#lockDatabase.
    def exitLockDatabase(self, ctx:OdpsParser.LockDatabaseContext):
        pass


    # Enter a parse tree produced by OdpsParser#lockMode.
    def enterLockMode(self, ctx:OdpsParser.LockModeContext):
        pass

    # Exit a parse tree produced by OdpsParser#lockMode.
    def exitLockMode(self, ctx:OdpsParser.LockModeContext):
        pass


    # Enter a parse tree produced by OdpsParser#unlockStatement.
    def enterUnlockStatement(self, ctx:OdpsParser.UnlockStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#unlockStatement.
    def exitUnlockStatement(self, ctx:OdpsParser.UnlockStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#unlockDatabase.
    def enterUnlockDatabase(self, ctx:OdpsParser.UnlockDatabaseContext):
        pass

    # Exit a parse tree produced by OdpsParser#unlockDatabase.
    def exitUnlockDatabase(self, ctx:OdpsParser.UnlockDatabaseContext):
        pass


    # Enter a parse tree produced by OdpsParser#resourceList.
    def enterResourceList(self, ctx:OdpsParser.ResourceListContext):
        pass

    # Exit a parse tree produced by OdpsParser#resourceList.
    def exitResourceList(self, ctx:OdpsParser.ResourceListContext):
        pass


    # Enter a parse tree produced by OdpsParser#resource.
    def enterResource(self, ctx:OdpsParser.ResourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#resource.
    def exitResource(self, ctx:OdpsParser.ResourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#resourceType.
    def enterResourceType(self, ctx:OdpsParser.ResourceTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#resourceType.
    def exitResourceType(self, ctx:OdpsParser.ResourceTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#createFunctionStatement.
    def enterCreateFunctionStatement(self, ctx:OdpsParser.CreateFunctionStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#createFunctionStatement.
    def exitCreateFunctionStatement(self, ctx:OdpsParser.CreateFunctionStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropFunctionStatement.
    def enterDropFunctionStatement(self, ctx:OdpsParser.DropFunctionStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropFunctionStatement.
    def exitDropFunctionStatement(self, ctx:OdpsParser.DropFunctionStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#reloadFunctionStatement.
    def enterReloadFunctionStatement(self, ctx:OdpsParser.ReloadFunctionStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#reloadFunctionStatement.
    def exitReloadFunctionStatement(self, ctx:OdpsParser.ReloadFunctionStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#createMacroStatement.
    def enterCreateMacroStatement(self, ctx:OdpsParser.CreateMacroStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#createMacroStatement.
    def exitCreateMacroStatement(self, ctx:OdpsParser.CreateMacroStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropMacroStatement.
    def enterDropMacroStatement(self, ctx:OdpsParser.DropMacroStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropMacroStatement.
    def exitDropMacroStatement(self, ctx:OdpsParser.DropMacroStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#createSqlFunctionStatement.
    def enterCreateSqlFunctionStatement(self, ctx:OdpsParser.CreateSqlFunctionStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#createSqlFunctionStatement.
    def exitCreateSqlFunctionStatement(self, ctx:OdpsParser.CreateSqlFunctionStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#cloneTableStatement.
    def enterCloneTableStatement(self, ctx:OdpsParser.CloneTableStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#cloneTableStatement.
    def exitCloneTableStatement(self, ctx:OdpsParser.CloneTableStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#createViewStatement.
    def enterCreateViewStatement(self, ctx:OdpsParser.CreateViewStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#createViewStatement.
    def exitCreateViewStatement(self, ctx:OdpsParser.CreateViewStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#viewPartition.
    def enterViewPartition(self, ctx:OdpsParser.ViewPartitionContext):
        pass

    # Exit a parse tree produced by OdpsParser#viewPartition.
    def exitViewPartition(self, ctx:OdpsParser.ViewPartitionContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropViewStatement.
    def enterDropViewStatement(self, ctx:OdpsParser.DropViewStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropViewStatement.
    def exitDropViewStatement(self, ctx:OdpsParser.DropViewStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#createMaterializedViewStatement.
    def enterCreateMaterializedViewStatement(self, ctx:OdpsParser.CreateMaterializedViewStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#createMaterializedViewStatement.
    def exitCreateMaterializedViewStatement(self, ctx:OdpsParser.CreateMaterializedViewStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropMaterializedViewStatement.
    def enterDropMaterializedViewStatement(self, ctx:OdpsParser.DropMaterializedViewStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropMaterializedViewStatement.
    def exitDropMaterializedViewStatement(self, ctx:OdpsParser.DropMaterializedViewStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#showFunctionIdentifier.
    def enterShowFunctionIdentifier(self, ctx:OdpsParser.ShowFunctionIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#showFunctionIdentifier.
    def exitShowFunctionIdentifier(self, ctx:OdpsParser.ShowFunctionIdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#showStmtIdentifier.
    def enterShowStmtIdentifier(self, ctx:OdpsParser.ShowStmtIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#showStmtIdentifier.
    def exitShowStmtIdentifier(self, ctx:OdpsParser.ShowStmtIdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableComment.
    def enterTableComment(self, ctx:OdpsParser.TableCommentContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableComment.
    def exitTableComment(self, ctx:OdpsParser.TableCommentContext):
        pass


    # Enter a parse tree produced by OdpsParser#tablePartition.
    def enterTablePartition(self, ctx:OdpsParser.TablePartitionContext):
        pass

    # Exit a parse tree produced by OdpsParser#tablePartition.
    def exitTablePartition(self, ctx:OdpsParser.TablePartitionContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableBuckets.
    def enterTableBuckets(self, ctx:OdpsParser.TableBucketsContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableBuckets.
    def exitTableBuckets(self, ctx:OdpsParser.TableBucketsContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableShards.
    def enterTableShards(self, ctx:OdpsParser.TableShardsContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableShards.
    def exitTableShards(self, ctx:OdpsParser.TableShardsContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableSkewed.
    def enterTableSkewed(self, ctx:OdpsParser.TableSkewedContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableSkewed.
    def exitTableSkewed(self, ctx:OdpsParser.TableSkewedContext):
        pass


    # Enter a parse tree produced by OdpsParser#rowFormat.
    def enterRowFormat(self, ctx:OdpsParser.RowFormatContext):
        pass

    # Exit a parse tree produced by OdpsParser#rowFormat.
    def exitRowFormat(self, ctx:OdpsParser.RowFormatContext):
        pass


    # Enter a parse tree produced by OdpsParser#recordReader.
    def enterRecordReader(self, ctx:OdpsParser.RecordReaderContext):
        pass

    # Exit a parse tree produced by OdpsParser#recordReader.
    def exitRecordReader(self, ctx:OdpsParser.RecordReaderContext):
        pass


    # Enter a parse tree produced by OdpsParser#recordWriter.
    def enterRecordWriter(self, ctx:OdpsParser.RecordWriterContext):
        pass

    # Exit a parse tree produced by OdpsParser#recordWriter.
    def exitRecordWriter(self, ctx:OdpsParser.RecordWriterContext):
        pass


    # Enter a parse tree produced by OdpsParser#rowFormatSerde.
    def enterRowFormatSerde(self, ctx:OdpsParser.RowFormatSerdeContext):
        pass

    # Exit a parse tree produced by OdpsParser#rowFormatSerde.
    def exitRowFormatSerde(self, ctx:OdpsParser.RowFormatSerdeContext):
        pass


    # Enter a parse tree produced by OdpsParser#rowFormatDelimited.
    def enterRowFormatDelimited(self, ctx:OdpsParser.RowFormatDelimitedContext):
        pass

    # Exit a parse tree produced by OdpsParser#rowFormatDelimited.
    def exitRowFormatDelimited(self, ctx:OdpsParser.RowFormatDelimitedContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableRowFormat.
    def enterTableRowFormat(self, ctx:OdpsParser.TableRowFormatContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableRowFormat.
    def exitTableRowFormat(self, ctx:OdpsParser.TableRowFormatContext):
        pass


    # Enter a parse tree produced by OdpsParser#tablePropertiesPrefixed.
    def enterTablePropertiesPrefixed(self, ctx:OdpsParser.TablePropertiesPrefixedContext):
        pass

    # Exit a parse tree produced by OdpsParser#tablePropertiesPrefixed.
    def exitTablePropertiesPrefixed(self, ctx:OdpsParser.TablePropertiesPrefixedContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableProperties.
    def enterTableProperties(self, ctx:OdpsParser.TablePropertiesContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableProperties.
    def exitTableProperties(self, ctx:OdpsParser.TablePropertiesContext):
        pass


    # Enter a parse tree produced by OdpsParser#tablePropertiesList.
    def enterTablePropertiesList(self, ctx:OdpsParser.TablePropertiesListContext):
        pass

    # Exit a parse tree produced by OdpsParser#tablePropertiesList.
    def exitTablePropertiesList(self, ctx:OdpsParser.TablePropertiesListContext):
        pass


    # Enter a parse tree produced by OdpsParser#keyValueProperty.
    def enterKeyValueProperty(self, ctx:OdpsParser.KeyValuePropertyContext):
        pass

    # Exit a parse tree produced by OdpsParser#keyValueProperty.
    def exitKeyValueProperty(self, ctx:OdpsParser.KeyValuePropertyContext):
        pass


    # Enter a parse tree produced by OdpsParser#userDefinedJoinPropertiesList.
    def enterUserDefinedJoinPropertiesList(self, ctx:OdpsParser.UserDefinedJoinPropertiesListContext):
        pass

    # Exit a parse tree produced by OdpsParser#userDefinedJoinPropertiesList.
    def exitUserDefinedJoinPropertiesList(self, ctx:OdpsParser.UserDefinedJoinPropertiesListContext):
        pass


    # Enter a parse tree produced by OdpsParser#keyPrivProperty.
    def enterKeyPrivProperty(self, ctx:OdpsParser.KeyPrivPropertyContext):
        pass

    # Exit a parse tree produced by OdpsParser#keyPrivProperty.
    def exitKeyPrivProperty(self, ctx:OdpsParser.KeyPrivPropertyContext):
        pass


    # Enter a parse tree produced by OdpsParser#keyProperty.
    def enterKeyProperty(self, ctx:OdpsParser.KeyPropertyContext):
        pass

    # Exit a parse tree produced by OdpsParser#keyProperty.
    def exitKeyProperty(self, ctx:OdpsParser.KeyPropertyContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableRowFormatFieldIdentifier.
    def enterTableRowFormatFieldIdentifier(self, ctx:OdpsParser.TableRowFormatFieldIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableRowFormatFieldIdentifier.
    def exitTableRowFormatFieldIdentifier(self, ctx:OdpsParser.TableRowFormatFieldIdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableRowFormatCollItemsIdentifier.
    def enterTableRowFormatCollItemsIdentifier(self, ctx:OdpsParser.TableRowFormatCollItemsIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableRowFormatCollItemsIdentifier.
    def exitTableRowFormatCollItemsIdentifier(self, ctx:OdpsParser.TableRowFormatCollItemsIdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableRowFormatMapKeysIdentifier.
    def enterTableRowFormatMapKeysIdentifier(self, ctx:OdpsParser.TableRowFormatMapKeysIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableRowFormatMapKeysIdentifier.
    def exitTableRowFormatMapKeysIdentifier(self, ctx:OdpsParser.TableRowFormatMapKeysIdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableRowFormatLinesIdentifier.
    def enterTableRowFormatLinesIdentifier(self, ctx:OdpsParser.TableRowFormatLinesIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableRowFormatLinesIdentifier.
    def exitTableRowFormatLinesIdentifier(self, ctx:OdpsParser.TableRowFormatLinesIdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableRowNullFormat.
    def enterTableRowNullFormat(self, ctx:OdpsParser.TableRowNullFormatContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableRowNullFormat.
    def exitTableRowNullFormat(self, ctx:OdpsParser.TableRowNullFormatContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableFileFormat.
    def enterTableFileFormat(self, ctx:OdpsParser.TableFileFormatContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableFileFormat.
    def exitTableFileFormat(self, ctx:OdpsParser.TableFileFormatContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableLocation.
    def enterTableLocation(self, ctx:OdpsParser.TableLocationContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableLocation.
    def exitTableLocation(self, ctx:OdpsParser.TableLocationContext):
        pass


    # Enter a parse tree produced by OdpsParser#externalTableResource.
    def enterExternalTableResource(self, ctx:OdpsParser.ExternalTableResourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#externalTableResource.
    def exitExternalTableResource(self, ctx:OdpsParser.ExternalTableResourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#viewResource.
    def enterViewResource(self, ctx:OdpsParser.ViewResourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#viewResource.
    def exitViewResource(self, ctx:OdpsParser.ViewResourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#outOfLineConstraints.
    def enterOutOfLineConstraints(self, ctx:OdpsParser.OutOfLineConstraintsContext):
        pass

    # Exit a parse tree produced by OdpsParser#outOfLineConstraints.
    def exitOutOfLineConstraints(self, ctx:OdpsParser.OutOfLineConstraintsContext):
        pass


    # Enter a parse tree produced by OdpsParser#enableSpec.
    def enterEnableSpec(self, ctx:OdpsParser.EnableSpecContext):
        pass

    # Exit a parse tree produced by OdpsParser#enableSpec.
    def exitEnableSpec(self, ctx:OdpsParser.EnableSpecContext):
        pass


    # Enter a parse tree produced by OdpsParser#validateSpec.
    def enterValidateSpec(self, ctx:OdpsParser.ValidateSpecContext):
        pass

    # Exit a parse tree produced by OdpsParser#validateSpec.
    def exitValidateSpec(self, ctx:OdpsParser.ValidateSpecContext):
        pass


    # Enter a parse tree produced by OdpsParser#relySpec.
    def enterRelySpec(self, ctx:OdpsParser.RelySpecContext):
        pass

    # Exit a parse tree produced by OdpsParser#relySpec.
    def exitRelySpec(self, ctx:OdpsParser.RelySpecContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameTypeConstraintList.
    def enterColumnNameTypeConstraintList(self, ctx:OdpsParser.ColumnNameTypeConstraintListContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameTypeConstraintList.
    def exitColumnNameTypeConstraintList(self, ctx:OdpsParser.ColumnNameTypeConstraintListContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameTypeList.
    def enterColumnNameTypeList(self, ctx:OdpsParser.ColumnNameTypeListContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameTypeList.
    def exitColumnNameTypeList(self, ctx:OdpsParser.ColumnNameTypeListContext):
        pass


    # Enter a parse tree produced by OdpsParser#partitionColumnNameTypeList.
    def enterPartitionColumnNameTypeList(self, ctx:OdpsParser.PartitionColumnNameTypeListContext):
        pass

    # Exit a parse tree produced by OdpsParser#partitionColumnNameTypeList.
    def exitPartitionColumnNameTypeList(self, ctx:OdpsParser.PartitionColumnNameTypeListContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameTypeConstraintWithPosList.
    def enterColumnNameTypeConstraintWithPosList(self, ctx:OdpsParser.ColumnNameTypeConstraintWithPosListContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameTypeConstraintWithPosList.
    def exitColumnNameTypeConstraintWithPosList(self, ctx:OdpsParser.ColumnNameTypeConstraintWithPosListContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameColonTypeList.
    def enterColumnNameColonTypeList(self, ctx:OdpsParser.ColumnNameColonTypeListContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameColonTypeList.
    def exitColumnNameColonTypeList(self, ctx:OdpsParser.ColumnNameColonTypeListContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameList.
    def enterColumnNameList(self, ctx:OdpsParser.ColumnNameListContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameList.
    def exitColumnNameList(self, ctx:OdpsParser.ColumnNameListContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameListInParentheses.
    def enterColumnNameListInParentheses(self, ctx:OdpsParser.ColumnNameListInParenthesesContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameListInParentheses.
    def exitColumnNameListInParentheses(self, ctx:OdpsParser.ColumnNameListInParenthesesContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnName.
    def enterColumnName(self, ctx:OdpsParser.ColumnNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnName.
    def exitColumnName(self, ctx:OdpsParser.ColumnNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameOrderList.
    def enterColumnNameOrderList(self, ctx:OdpsParser.ColumnNameOrderListContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameOrderList.
    def exitColumnNameOrderList(self, ctx:OdpsParser.ColumnNameOrderListContext):
        pass


    # Enter a parse tree produced by OdpsParser#clusterColumnNameOrderList.
    def enterClusterColumnNameOrderList(self, ctx:OdpsParser.ClusterColumnNameOrderListContext):
        pass

    # Exit a parse tree produced by OdpsParser#clusterColumnNameOrderList.
    def exitClusterColumnNameOrderList(self, ctx:OdpsParser.ClusterColumnNameOrderListContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewedValueElement.
    def enterSkewedValueElement(self, ctx:OdpsParser.SkewedValueElementContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewedValueElement.
    def exitSkewedValueElement(self, ctx:OdpsParser.SkewedValueElementContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewedColumnValuePairList.
    def enterSkewedColumnValuePairList(self, ctx:OdpsParser.SkewedColumnValuePairListContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewedColumnValuePairList.
    def exitSkewedColumnValuePairList(self, ctx:OdpsParser.SkewedColumnValuePairListContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewedColumnValuePair.
    def enterSkewedColumnValuePair(self, ctx:OdpsParser.SkewedColumnValuePairContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewedColumnValuePair.
    def exitSkewedColumnValuePair(self, ctx:OdpsParser.SkewedColumnValuePairContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewedColumnValues.
    def enterSkewedColumnValues(self, ctx:OdpsParser.SkewedColumnValuesContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewedColumnValues.
    def exitSkewedColumnValues(self, ctx:OdpsParser.SkewedColumnValuesContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewedColumnValue.
    def enterSkewedColumnValue(self, ctx:OdpsParser.SkewedColumnValueContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewedColumnValue.
    def exitSkewedColumnValue(self, ctx:OdpsParser.SkewedColumnValueContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewedValueLocationElement.
    def enterSkewedValueLocationElement(self, ctx:OdpsParser.SkewedValueLocationElementContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewedValueLocationElement.
    def exitSkewedValueLocationElement(self, ctx:OdpsParser.SkewedValueLocationElementContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameOrder.
    def enterColumnNameOrder(self, ctx:OdpsParser.ColumnNameOrderContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameOrder.
    def exitColumnNameOrder(self, ctx:OdpsParser.ColumnNameOrderContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameCommentList.
    def enterColumnNameCommentList(self, ctx:OdpsParser.ColumnNameCommentListContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameCommentList.
    def exitColumnNameCommentList(self, ctx:OdpsParser.ColumnNameCommentListContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameComment.
    def enterColumnNameComment(self, ctx:OdpsParser.ColumnNameCommentContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameComment.
    def exitColumnNameComment(self, ctx:OdpsParser.ColumnNameCommentContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnRefOrder.
    def enterColumnRefOrder(self, ctx:OdpsParser.ColumnRefOrderContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnRefOrder.
    def exitColumnRefOrder(self, ctx:OdpsParser.ColumnRefOrderContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameTypeConstraint.
    def enterColumnNameTypeConstraint(self, ctx:OdpsParser.ColumnNameTypeConstraintContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameTypeConstraint.
    def exitColumnNameTypeConstraint(self, ctx:OdpsParser.ColumnNameTypeConstraintContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameType.
    def enterColumnNameType(self, ctx:OdpsParser.ColumnNameTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameType.
    def exitColumnNameType(self, ctx:OdpsParser.ColumnNameTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#partitionColumnNameType.
    def enterPartitionColumnNameType(self, ctx:OdpsParser.PartitionColumnNameTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#partitionColumnNameType.
    def exitPartitionColumnNameType(self, ctx:OdpsParser.PartitionColumnNameTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#multipartIdentifier.
    def enterMultipartIdentifier(self, ctx:OdpsParser.MultipartIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#multipartIdentifier.
    def exitMultipartIdentifier(self, ctx:OdpsParser.MultipartIdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameTypeConstraintWithPos.
    def enterColumnNameTypeConstraintWithPos(self, ctx:OdpsParser.ColumnNameTypeConstraintWithPosContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameTypeConstraintWithPos.
    def exitColumnNameTypeConstraintWithPos(self, ctx:OdpsParser.ColumnNameTypeConstraintWithPosContext):
        pass


    # Enter a parse tree produced by OdpsParser#constraints.
    def enterConstraints(self, ctx:OdpsParser.ConstraintsContext):
        pass

    # Exit a parse tree produced by OdpsParser#constraints.
    def exitConstraints(self, ctx:OdpsParser.ConstraintsContext):
        pass


    # Enter a parse tree produced by OdpsParser#primaryKey.
    def enterPrimaryKey(self, ctx:OdpsParser.PrimaryKeyContext):
        pass

    # Exit a parse tree produced by OdpsParser#primaryKey.
    def exitPrimaryKey(self, ctx:OdpsParser.PrimaryKeyContext):
        pass


    # Enter a parse tree produced by OdpsParser#nullableSpec.
    def enterNullableSpec(self, ctx:OdpsParser.NullableSpecContext):
        pass

    # Exit a parse tree produced by OdpsParser#nullableSpec.
    def exitNullableSpec(self, ctx:OdpsParser.NullableSpecContext):
        pass


    # Enter a parse tree produced by OdpsParser#defaultValue.
    def enterDefaultValue(self, ctx:OdpsParser.DefaultValueContext):
        pass

    # Exit a parse tree produced by OdpsParser#defaultValue.
    def exitDefaultValue(self, ctx:OdpsParser.DefaultValueContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameColonType.
    def enterColumnNameColonType(self, ctx:OdpsParser.ColumnNameColonTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameColonType.
    def exitColumnNameColonType(self, ctx:OdpsParser.ColumnNameColonTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#colType.
    def enterColType(self, ctx:OdpsParser.ColTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#colType.
    def exitColType(self, ctx:OdpsParser.ColTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#colTypeList.
    def enterColTypeList(self, ctx:OdpsParser.ColTypeListContext):
        pass

    # Exit a parse tree produced by OdpsParser#colTypeList.
    def exitColTypeList(self, ctx:OdpsParser.ColTypeListContext):
        pass


    # Enter a parse tree produced by OdpsParser#anyType.
    def enterAnyType(self, ctx:OdpsParser.AnyTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#anyType.
    def exitAnyType(self, ctx:OdpsParser.AnyTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#anyTypeList.
    def enterAnyTypeList(self, ctx:OdpsParser.AnyTypeListContext):
        pass

    # Exit a parse tree produced by OdpsParser#anyTypeList.
    def exitAnyTypeList(self, ctx:OdpsParser.AnyTypeListContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableTypeInfo.
    def enterTableTypeInfo(self, ctx:OdpsParser.TableTypeInfoContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableTypeInfo.
    def exitTableTypeInfo(self, ctx:OdpsParser.TableTypeInfoContext):
        pass


    # Enter a parse tree produced by OdpsParser#type.
    def enterType(self, ctx:OdpsParser.TypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#type.
    def exitType(self, ctx:OdpsParser.TypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#primitiveType.
    def enterPrimitiveType(self, ctx:OdpsParser.PrimitiveTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#primitiveType.
    def exitPrimitiveType(self, ctx:OdpsParser.PrimitiveTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#builtinTypeOrUdt.
    def enterBuiltinTypeOrUdt(self, ctx:OdpsParser.BuiltinTypeOrUdtContext):
        pass

    # Exit a parse tree produced by OdpsParser#builtinTypeOrUdt.
    def exitBuiltinTypeOrUdt(self, ctx:OdpsParser.BuiltinTypeOrUdtContext):
        pass


    # Enter a parse tree produced by OdpsParser#primitiveTypeOrUdt.
    def enterPrimitiveTypeOrUdt(self, ctx:OdpsParser.PrimitiveTypeOrUdtContext):
        pass

    # Exit a parse tree produced by OdpsParser#primitiveTypeOrUdt.
    def exitPrimitiveTypeOrUdt(self, ctx:OdpsParser.PrimitiveTypeOrUdtContext):
        pass


    # Enter a parse tree produced by OdpsParser#listType.
    def enterListType(self, ctx:OdpsParser.ListTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#listType.
    def exitListType(self, ctx:OdpsParser.ListTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#structType.
    def enterStructType(self, ctx:OdpsParser.StructTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#structType.
    def exitStructType(self, ctx:OdpsParser.StructTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#mapType.
    def enterMapType(self, ctx:OdpsParser.MapTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#mapType.
    def exitMapType(self, ctx:OdpsParser.MapTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#unionType.
    def enterUnionType(self, ctx:OdpsParser.UnionTypeContext):
        pass

    # Exit a parse tree produced by OdpsParser#unionType.
    def exitUnionType(self, ctx:OdpsParser.UnionTypeContext):
        pass


    # Enter a parse tree produced by OdpsParser#setOperator.
    def enterSetOperator(self, ctx:OdpsParser.SetOperatorContext):
        pass

    # Exit a parse tree produced by OdpsParser#setOperator.
    def exitSetOperator(self, ctx:OdpsParser.SetOperatorContext):
        pass


    # Enter a parse tree produced by OdpsParser#withClause.
    def enterWithClause(self, ctx:OdpsParser.WithClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#withClause.
    def exitWithClause(self, ctx:OdpsParser.WithClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#insertClause.
    def enterInsertClause(self, ctx:OdpsParser.InsertClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#insertClause.
    def exitInsertClause(self, ctx:OdpsParser.InsertClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#destination.
    def enterDestination(self, ctx:OdpsParser.DestinationContext):
        pass

    # Exit a parse tree produced by OdpsParser#destination.
    def exitDestination(self, ctx:OdpsParser.DestinationContext):
        pass


    # Enter a parse tree produced by OdpsParser#deleteStatement.
    def enterDeleteStatement(self, ctx:OdpsParser.DeleteStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#deleteStatement.
    def exitDeleteStatement(self, ctx:OdpsParser.DeleteStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnAssignmentClause.
    def enterColumnAssignmentClause(self, ctx:OdpsParser.ColumnAssignmentClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnAssignmentClause.
    def exitColumnAssignmentClause(self, ctx:OdpsParser.ColumnAssignmentClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#setColumnsClause.
    def enterSetColumnsClause(self, ctx:OdpsParser.SetColumnsClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#setColumnsClause.
    def exitSetColumnsClause(self, ctx:OdpsParser.SetColumnsClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#updateStatement.
    def enterUpdateStatement(self, ctx:OdpsParser.UpdateStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#updateStatement.
    def exitUpdateStatement(self, ctx:OdpsParser.UpdateStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#mergeStatement.
    def enterMergeStatement(self, ctx:OdpsParser.MergeStatementContext):
        pass

    # Exit a parse tree produced by OdpsParser#mergeStatement.
    def exitMergeStatement(self, ctx:OdpsParser.MergeStatementContext):
        pass


    # Enter a parse tree produced by OdpsParser#mergeTargetTable.
    def enterMergeTargetTable(self, ctx:OdpsParser.MergeTargetTableContext):
        pass

    # Exit a parse tree produced by OdpsParser#mergeTargetTable.
    def exitMergeTargetTable(self, ctx:OdpsParser.MergeTargetTableContext):
        pass


    # Enter a parse tree produced by OdpsParser#mergeSourceTable.
    def enterMergeSourceTable(self, ctx:OdpsParser.MergeSourceTableContext):
        pass

    # Exit a parse tree produced by OdpsParser#mergeSourceTable.
    def exitMergeSourceTable(self, ctx:OdpsParser.MergeSourceTableContext):
        pass


    # Enter a parse tree produced by OdpsParser#mergeAction.
    def enterMergeAction(self, ctx:OdpsParser.MergeActionContext):
        pass

    # Exit a parse tree produced by OdpsParser#mergeAction.
    def exitMergeAction(self, ctx:OdpsParser.MergeActionContext):
        pass


    # Enter a parse tree produced by OdpsParser#mergeValuesCaluse.
    def enterMergeValuesCaluse(self, ctx:OdpsParser.MergeValuesCaluseContext):
        pass

    # Exit a parse tree produced by OdpsParser#mergeValuesCaluse.
    def exitMergeValuesCaluse(self, ctx:OdpsParser.MergeValuesCaluseContext):
        pass


    # Enter a parse tree produced by OdpsParser#mergeSetColumnsClause.
    def enterMergeSetColumnsClause(self, ctx:OdpsParser.MergeSetColumnsClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#mergeSetColumnsClause.
    def exitMergeSetColumnsClause(self, ctx:OdpsParser.MergeSetColumnsClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#mergeColumnAssignmentClause.
    def enterMergeColumnAssignmentClause(self, ctx:OdpsParser.MergeColumnAssignmentClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#mergeColumnAssignmentClause.
    def exitMergeColumnAssignmentClause(self, ctx:OdpsParser.MergeColumnAssignmentClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#selectClause.
    def enterSelectClause(self, ctx:OdpsParser.SelectClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#selectClause.
    def exitSelectClause(self, ctx:OdpsParser.SelectClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#selectList.
    def enterSelectList(self, ctx:OdpsParser.SelectListContext):
        pass

    # Exit a parse tree produced by OdpsParser#selectList.
    def exitSelectList(self, ctx:OdpsParser.SelectListContext):
        pass


    # Enter a parse tree produced by OdpsParser#selectTrfmClause.
    def enterSelectTrfmClause(self, ctx:OdpsParser.SelectTrfmClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#selectTrfmClause.
    def exitSelectTrfmClause(self, ctx:OdpsParser.SelectTrfmClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#hintClause.
    def enterHintClause(self, ctx:OdpsParser.HintClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#hintClause.
    def exitHintClause(self, ctx:OdpsParser.HintClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#hintList.
    def enterHintList(self, ctx:OdpsParser.HintListContext):
        pass

    # Exit a parse tree produced by OdpsParser#hintList.
    def exitHintList(self, ctx:OdpsParser.HintListContext):
        pass


    # Enter a parse tree produced by OdpsParser#hintItem.
    def enterHintItem(self, ctx:OdpsParser.HintItemContext):
        pass

    # Exit a parse tree produced by OdpsParser#hintItem.
    def exitHintItem(self, ctx:OdpsParser.HintItemContext):
        pass


    # Enter a parse tree produced by OdpsParser#dynamicfilterHint.
    def enterDynamicfilterHint(self, ctx:OdpsParser.DynamicfilterHintContext):
        pass

    # Exit a parse tree produced by OdpsParser#dynamicfilterHint.
    def exitDynamicfilterHint(self, ctx:OdpsParser.DynamicfilterHintContext):
        pass


    # Enter a parse tree produced by OdpsParser#mapJoinHint.
    def enterMapJoinHint(self, ctx:OdpsParser.MapJoinHintContext):
        pass

    # Exit a parse tree produced by OdpsParser#mapJoinHint.
    def exitMapJoinHint(self, ctx:OdpsParser.MapJoinHintContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewJoinHint.
    def enterSkewJoinHint(self, ctx:OdpsParser.SkewJoinHintContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewJoinHint.
    def exitSkewJoinHint(self, ctx:OdpsParser.SkewJoinHintContext):
        pass


    # Enter a parse tree produced by OdpsParser#selectivityHint.
    def enterSelectivityHint(self, ctx:OdpsParser.SelectivityHintContext):
        pass

    # Exit a parse tree produced by OdpsParser#selectivityHint.
    def exitSelectivityHint(self, ctx:OdpsParser.SelectivityHintContext):
        pass


    # Enter a parse tree produced by OdpsParser#multipleSkewHintArgs.
    def enterMultipleSkewHintArgs(self, ctx:OdpsParser.MultipleSkewHintArgsContext):
        pass

    # Exit a parse tree produced by OdpsParser#multipleSkewHintArgs.
    def exitMultipleSkewHintArgs(self, ctx:OdpsParser.MultipleSkewHintArgsContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewJoinHintArgs.
    def enterSkewJoinHintArgs(self, ctx:OdpsParser.SkewJoinHintArgsContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewJoinHintArgs.
    def exitSkewJoinHintArgs(self, ctx:OdpsParser.SkewJoinHintArgsContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewColumns.
    def enterSkewColumns(self, ctx:OdpsParser.SkewColumnsContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewColumns.
    def exitSkewColumns(self, ctx:OdpsParser.SkewColumnsContext):
        pass


    # Enter a parse tree produced by OdpsParser#skewJoinHintKeyValues.
    def enterSkewJoinHintKeyValues(self, ctx:OdpsParser.SkewJoinHintKeyValuesContext):
        pass

    # Exit a parse tree produced by OdpsParser#skewJoinHintKeyValues.
    def exitSkewJoinHintKeyValues(self, ctx:OdpsParser.SkewJoinHintKeyValuesContext):
        pass


    # Enter a parse tree produced by OdpsParser#hintName.
    def enterHintName(self, ctx:OdpsParser.HintNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#hintName.
    def exitHintName(self, ctx:OdpsParser.HintNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#hintArgs.
    def enterHintArgs(self, ctx:OdpsParser.HintArgsContext):
        pass

    # Exit a parse tree produced by OdpsParser#hintArgs.
    def exitHintArgs(self, ctx:OdpsParser.HintArgsContext):
        pass


    # Enter a parse tree produced by OdpsParser#hintArgName.
    def enterHintArgName(self, ctx:OdpsParser.HintArgNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#hintArgName.
    def exitHintArgName(self, ctx:OdpsParser.HintArgNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#selectItem.
    def enterSelectItem(self, ctx:OdpsParser.SelectItemContext):
        pass

    # Exit a parse tree produced by OdpsParser#selectItem.
    def exitSelectItem(self, ctx:OdpsParser.SelectItemContext):
        pass


    # Enter a parse tree produced by OdpsParser#trfmClause.
    def enterTrfmClause(self, ctx:OdpsParser.TrfmClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#trfmClause.
    def exitTrfmClause(self, ctx:OdpsParser.TrfmClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#selectExpression.
    def enterSelectExpression(self, ctx:OdpsParser.SelectExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#selectExpression.
    def exitSelectExpression(self, ctx:OdpsParser.SelectExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#selectExpressionList.
    def enterSelectExpressionList(self, ctx:OdpsParser.SelectExpressionListContext):
        pass

    # Exit a parse tree produced by OdpsParser#selectExpressionList.
    def exitSelectExpressionList(self, ctx:OdpsParser.SelectExpressionListContext):
        pass


    # Enter a parse tree produced by OdpsParser#window_clause.
    def enterWindow_clause(self, ctx:OdpsParser.Window_clauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#window_clause.
    def exitWindow_clause(self, ctx:OdpsParser.Window_clauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#window_defn.
    def enterWindow_defn(self, ctx:OdpsParser.Window_defnContext):
        pass

    # Exit a parse tree produced by OdpsParser#window_defn.
    def exitWindow_defn(self, ctx:OdpsParser.Window_defnContext):
        pass


    # Enter a parse tree produced by OdpsParser#window_specification.
    def enterWindow_specification(self, ctx:OdpsParser.Window_specificationContext):
        pass

    # Exit a parse tree produced by OdpsParser#window_specification.
    def exitWindow_specification(self, ctx:OdpsParser.Window_specificationContext):
        pass


    # Enter a parse tree produced by OdpsParser#window_frame.
    def enterWindow_frame(self, ctx:OdpsParser.Window_frameContext):
        pass

    # Exit a parse tree produced by OdpsParser#window_frame.
    def exitWindow_frame(self, ctx:OdpsParser.Window_frameContext):
        pass


    # Enter a parse tree produced by OdpsParser#frame_exclusion.
    def enterFrame_exclusion(self, ctx:OdpsParser.Frame_exclusionContext):
        pass

    # Exit a parse tree produced by OdpsParser#frame_exclusion.
    def exitFrame_exclusion(self, ctx:OdpsParser.Frame_exclusionContext):
        pass


    # Enter a parse tree produced by OdpsParser#window_frame_start_boundary.
    def enterWindow_frame_start_boundary(self, ctx:OdpsParser.Window_frame_start_boundaryContext):
        pass

    # Exit a parse tree produced by OdpsParser#window_frame_start_boundary.
    def exitWindow_frame_start_boundary(self, ctx:OdpsParser.Window_frame_start_boundaryContext):
        pass


    # Enter a parse tree produced by OdpsParser#window_frame_boundary.
    def enterWindow_frame_boundary(self, ctx:OdpsParser.Window_frame_boundaryContext):
        pass

    # Exit a parse tree produced by OdpsParser#window_frame_boundary.
    def exitWindow_frame_boundary(self, ctx:OdpsParser.Window_frame_boundaryContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableAllColumns.
    def enterTableAllColumns(self, ctx:OdpsParser.TableAllColumnsContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableAllColumns.
    def exitTableAllColumns(self, ctx:OdpsParser.TableAllColumnsContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableOrColumn.
    def enterTableOrColumn(self, ctx:OdpsParser.TableOrColumnContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableOrColumn.
    def exitTableOrColumn(self, ctx:OdpsParser.TableOrColumnContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableAndColumnRef.
    def enterTableAndColumnRef(self, ctx:OdpsParser.TableAndColumnRefContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableAndColumnRef.
    def exitTableAndColumnRef(self, ctx:OdpsParser.TableAndColumnRefContext):
        pass


    # Enter a parse tree produced by OdpsParser#expressionList.
    def enterExpressionList(self, ctx:OdpsParser.ExpressionListContext):
        pass

    # Exit a parse tree produced by OdpsParser#expressionList.
    def exitExpressionList(self, ctx:OdpsParser.ExpressionListContext):
        pass


    # Enter a parse tree produced by OdpsParser#aliasList.
    def enterAliasList(self, ctx:OdpsParser.AliasListContext):
        pass

    # Exit a parse tree produced by OdpsParser#aliasList.
    def exitAliasList(self, ctx:OdpsParser.AliasListContext):
        pass


    # Enter a parse tree produced by OdpsParser#fromClause.
    def enterFromClause(self, ctx:OdpsParser.FromClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#fromClause.
    def exitFromClause(self, ctx:OdpsParser.FromClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#joinSource.
    def enterJoinSource(self, ctx:OdpsParser.JoinSourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#joinSource.
    def exitJoinSource(self, ctx:OdpsParser.JoinSourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#joinRHS.
    def enterJoinRHS(self, ctx:OdpsParser.JoinRHSContext):
        pass

    # Exit a parse tree produced by OdpsParser#joinRHS.
    def exitJoinRHS(self, ctx:OdpsParser.JoinRHSContext):
        pass


    # Enter a parse tree produced by OdpsParser#uniqueJoinSource.
    def enterUniqueJoinSource(self, ctx:OdpsParser.UniqueJoinSourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#uniqueJoinSource.
    def exitUniqueJoinSource(self, ctx:OdpsParser.UniqueJoinSourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#uniqueJoinExpr.
    def enterUniqueJoinExpr(self, ctx:OdpsParser.UniqueJoinExprContext):
        pass

    # Exit a parse tree produced by OdpsParser#uniqueJoinExpr.
    def exitUniqueJoinExpr(self, ctx:OdpsParser.UniqueJoinExprContext):
        pass


    # Enter a parse tree produced by OdpsParser#uniqueJoinToken.
    def enterUniqueJoinToken(self, ctx:OdpsParser.UniqueJoinTokenContext):
        pass

    # Exit a parse tree produced by OdpsParser#uniqueJoinToken.
    def exitUniqueJoinToken(self, ctx:OdpsParser.UniqueJoinTokenContext):
        pass


    # Enter a parse tree produced by OdpsParser#joinToken.
    def enterJoinToken(self, ctx:OdpsParser.JoinTokenContext):
        pass

    # Exit a parse tree produced by OdpsParser#joinToken.
    def exitJoinToken(self, ctx:OdpsParser.JoinTokenContext):
        pass


    # Enter a parse tree produced by OdpsParser#lateralView.
    def enterLateralView(self, ctx:OdpsParser.LateralViewContext):
        pass

    # Exit a parse tree produced by OdpsParser#lateralView.
    def exitLateralView(self, ctx:OdpsParser.LateralViewContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableAlias.
    def enterTableAlias(self, ctx:OdpsParser.TableAliasContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableAlias.
    def exitTableAlias(self, ctx:OdpsParser.TableAliasContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableBucketSample.
    def enterTableBucketSample(self, ctx:OdpsParser.TableBucketSampleContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableBucketSample.
    def exitTableBucketSample(self, ctx:OdpsParser.TableBucketSampleContext):
        pass


    # Enter a parse tree produced by OdpsParser#splitSample.
    def enterSplitSample(self, ctx:OdpsParser.SplitSampleContext):
        pass

    # Exit a parse tree produced by OdpsParser#splitSample.
    def exitSplitSample(self, ctx:OdpsParser.SplitSampleContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableSample.
    def enterTableSample(self, ctx:OdpsParser.TableSampleContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableSample.
    def exitTableSample(self, ctx:OdpsParser.TableSampleContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableSource.
    def enterTableSource(self, ctx:OdpsParser.TableSourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableSource.
    def exitTableSource(self, ctx:OdpsParser.TableSourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#availableSql11KeywordsForOdpsTableAlias.
    def enterAvailableSql11KeywordsForOdpsTableAlias(self, ctx:OdpsParser.AvailableSql11KeywordsForOdpsTableAliasContext):
        pass

    # Exit a parse tree produced by OdpsParser#availableSql11KeywordsForOdpsTableAlias.
    def exitAvailableSql11KeywordsForOdpsTableAlias(self, ctx:OdpsParser.AvailableSql11KeywordsForOdpsTableAliasContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableName.
    def enterTableName(self, ctx:OdpsParser.TableNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableName.
    def exitTableName(self, ctx:OdpsParser.TableNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#partitioningSpec.
    def enterPartitioningSpec(self, ctx:OdpsParser.PartitioningSpecContext):
        pass

    # Exit a parse tree produced by OdpsParser#partitioningSpec.
    def exitPartitioningSpec(self, ctx:OdpsParser.PartitioningSpecContext):
        pass


    # Enter a parse tree produced by OdpsParser#partitionTableFunctionSource.
    def enterPartitionTableFunctionSource(self, ctx:OdpsParser.PartitionTableFunctionSourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#partitionTableFunctionSource.
    def exitPartitionTableFunctionSource(self, ctx:OdpsParser.PartitionTableFunctionSourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#partitionedTableFunction.
    def enterPartitionedTableFunction(self, ctx:OdpsParser.PartitionedTableFunctionContext):
        pass

    # Exit a parse tree produced by OdpsParser#partitionedTableFunction.
    def exitPartitionedTableFunction(self, ctx:OdpsParser.PartitionedTableFunctionContext):
        pass


    # Enter a parse tree produced by OdpsParser#whereClause.
    def enterWhereClause(self, ctx:OdpsParser.WhereClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#whereClause.
    def exitWhereClause(self, ctx:OdpsParser.WhereClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#valueRowConstructor.
    def enterValueRowConstructor(self, ctx:OdpsParser.ValueRowConstructorContext):
        pass

    # Exit a parse tree produced by OdpsParser#valueRowConstructor.
    def exitValueRowConstructor(self, ctx:OdpsParser.ValueRowConstructorContext):
        pass


    # Enter a parse tree produced by OdpsParser#valuesTableConstructor.
    def enterValuesTableConstructor(self, ctx:OdpsParser.ValuesTableConstructorContext):
        pass

    # Exit a parse tree produced by OdpsParser#valuesTableConstructor.
    def exitValuesTableConstructor(self, ctx:OdpsParser.ValuesTableConstructorContext):
        pass


    # Enter a parse tree produced by OdpsParser#valuesClause.
    def enterValuesClause(self, ctx:OdpsParser.ValuesClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#valuesClause.
    def exitValuesClause(self, ctx:OdpsParser.ValuesClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#virtualTableSource.
    def enterVirtualTableSource(self, ctx:OdpsParser.VirtualTableSourceContext):
        pass

    # Exit a parse tree produced by OdpsParser#virtualTableSource.
    def exitVirtualTableSource(self, ctx:OdpsParser.VirtualTableSourceContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableNameColList.
    def enterTableNameColList(self, ctx:OdpsParser.TableNameColListContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableNameColList.
    def exitTableNameColList(self, ctx:OdpsParser.TableNameColListContext):
        pass


    # Enter a parse tree produced by OdpsParser#functionTypeCubeOrRollup.
    def enterFunctionTypeCubeOrRollup(self, ctx:OdpsParser.FunctionTypeCubeOrRollupContext):
        pass

    # Exit a parse tree produced by OdpsParser#functionTypeCubeOrRollup.
    def exitFunctionTypeCubeOrRollup(self, ctx:OdpsParser.FunctionTypeCubeOrRollupContext):
        pass


    # Enter a parse tree produced by OdpsParser#groupingSetsItem.
    def enterGroupingSetsItem(self, ctx:OdpsParser.GroupingSetsItemContext):
        pass

    # Exit a parse tree produced by OdpsParser#groupingSetsItem.
    def exitGroupingSetsItem(self, ctx:OdpsParser.GroupingSetsItemContext):
        pass


    # Enter a parse tree produced by OdpsParser#groupingSetsClause.
    def enterGroupingSetsClause(self, ctx:OdpsParser.GroupingSetsClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#groupingSetsClause.
    def exitGroupingSetsClause(self, ctx:OdpsParser.GroupingSetsClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#groupByKey.
    def enterGroupByKey(self, ctx:OdpsParser.GroupByKeyContext):
        pass

    # Exit a parse tree produced by OdpsParser#groupByKey.
    def exitGroupByKey(self, ctx:OdpsParser.GroupByKeyContext):
        pass


    # Enter a parse tree produced by OdpsParser#groupByClause.
    def enterGroupByClause(self, ctx:OdpsParser.GroupByClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#groupByClause.
    def exitGroupByClause(self, ctx:OdpsParser.GroupByClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#groupingSetExpression.
    def enterGroupingSetExpression(self, ctx:OdpsParser.GroupingSetExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#groupingSetExpression.
    def exitGroupingSetExpression(self, ctx:OdpsParser.GroupingSetExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#groupingSetExpressionMultiple.
    def enterGroupingSetExpressionMultiple(self, ctx:OdpsParser.GroupingSetExpressionMultipleContext):
        pass

    # Exit a parse tree produced by OdpsParser#groupingSetExpressionMultiple.
    def exitGroupingSetExpressionMultiple(self, ctx:OdpsParser.GroupingSetExpressionMultipleContext):
        pass


    # Enter a parse tree produced by OdpsParser#groupingExpressionSingle.
    def enterGroupingExpressionSingle(self, ctx:OdpsParser.GroupingExpressionSingleContext):
        pass

    # Exit a parse tree produced by OdpsParser#groupingExpressionSingle.
    def exitGroupingExpressionSingle(self, ctx:OdpsParser.GroupingExpressionSingleContext):
        pass


    # Enter a parse tree produced by OdpsParser#havingClause.
    def enterHavingClause(self, ctx:OdpsParser.HavingClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#havingClause.
    def exitHavingClause(self, ctx:OdpsParser.HavingClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#havingCondition.
    def enterHavingCondition(self, ctx:OdpsParser.HavingConditionContext):
        pass

    # Exit a parse tree produced by OdpsParser#havingCondition.
    def exitHavingCondition(self, ctx:OdpsParser.HavingConditionContext):
        pass


    # Enter a parse tree produced by OdpsParser#expressionsInParenthese.
    def enterExpressionsInParenthese(self, ctx:OdpsParser.ExpressionsInParentheseContext):
        pass

    # Exit a parse tree produced by OdpsParser#expressionsInParenthese.
    def exitExpressionsInParenthese(self, ctx:OdpsParser.ExpressionsInParentheseContext):
        pass


    # Enter a parse tree produced by OdpsParser#expressionsNotInParenthese.
    def enterExpressionsNotInParenthese(self, ctx:OdpsParser.ExpressionsNotInParentheseContext):
        pass

    # Exit a parse tree produced by OdpsParser#expressionsNotInParenthese.
    def exitExpressionsNotInParenthese(self, ctx:OdpsParser.ExpressionsNotInParentheseContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnRefOrderInParenthese.
    def enterColumnRefOrderInParenthese(self, ctx:OdpsParser.ColumnRefOrderInParentheseContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnRefOrderInParenthese.
    def exitColumnRefOrderInParenthese(self, ctx:OdpsParser.ColumnRefOrderInParentheseContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnRefOrderNotInParenthese.
    def enterColumnRefOrderNotInParenthese(self, ctx:OdpsParser.ColumnRefOrderNotInParentheseContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnRefOrderNotInParenthese.
    def exitColumnRefOrderNotInParenthese(self, ctx:OdpsParser.ColumnRefOrderNotInParentheseContext):
        pass


    # Enter a parse tree produced by OdpsParser#orderByClause.
    def enterOrderByClause(self, ctx:OdpsParser.OrderByClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#orderByClause.
    def exitOrderByClause(self, ctx:OdpsParser.OrderByClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameOrIndexInParenthese.
    def enterColumnNameOrIndexInParenthese(self, ctx:OdpsParser.ColumnNameOrIndexInParentheseContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameOrIndexInParenthese.
    def exitColumnNameOrIndexInParenthese(self, ctx:OdpsParser.ColumnNameOrIndexInParentheseContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameOrIndexNotInParenthese.
    def enterColumnNameOrIndexNotInParenthese(self, ctx:OdpsParser.ColumnNameOrIndexNotInParentheseContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameOrIndexNotInParenthese.
    def exitColumnNameOrIndexNotInParenthese(self, ctx:OdpsParser.ColumnNameOrIndexNotInParentheseContext):
        pass


    # Enter a parse tree produced by OdpsParser#columnNameOrIndex.
    def enterColumnNameOrIndex(self, ctx:OdpsParser.ColumnNameOrIndexContext):
        pass

    # Exit a parse tree produced by OdpsParser#columnNameOrIndex.
    def exitColumnNameOrIndex(self, ctx:OdpsParser.ColumnNameOrIndexContext):
        pass


    # Enter a parse tree produced by OdpsParser#zorderByClause.
    def enterZorderByClause(self, ctx:OdpsParser.ZorderByClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#zorderByClause.
    def exitZorderByClause(self, ctx:OdpsParser.ZorderByClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#clusterByClause.
    def enterClusterByClause(self, ctx:OdpsParser.ClusterByClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#clusterByClause.
    def exitClusterByClause(self, ctx:OdpsParser.ClusterByClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#partitionByClause.
    def enterPartitionByClause(self, ctx:OdpsParser.PartitionByClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#partitionByClause.
    def exitPartitionByClause(self, ctx:OdpsParser.PartitionByClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#distributeByClause.
    def enterDistributeByClause(self, ctx:OdpsParser.DistributeByClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#distributeByClause.
    def exitDistributeByClause(self, ctx:OdpsParser.DistributeByClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#sortByClause.
    def enterSortByClause(self, ctx:OdpsParser.SortByClauseContext):
        pass

    # Exit a parse tree produced by OdpsParser#sortByClause.
    def exitSortByClause(self, ctx:OdpsParser.SortByClauseContext):
        pass


    # Enter a parse tree produced by OdpsParser#function.
    def enterFunction(self, ctx:OdpsParser.FunctionContext):
        pass

    # Exit a parse tree produced by OdpsParser#function.
    def exitFunction(self, ctx:OdpsParser.FunctionContext):
        pass


    # Enter a parse tree produced by OdpsParser#functionArgument.
    def enterFunctionArgument(self, ctx:OdpsParser.FunctionArgumentContext):
        pass

    # Exit a parse tree produced by OdpsParser#functionArgument.
    def exitFunctionArgument(self, ctx:OdpsParser.FunctionArgumentContext):
        pass


    # Enter a parse tree produced by OdpsParser#builtinFunctionStructure.
    def enterBuiltinFunctionStructure(self, ctx:OdpsParser.BuiltinFunctionStructureContext):
        pass

    # Exit a parse tree produced by OdpsParser#builtinFunctionStructure.
    def exitBuiltinFunctionStructure(self, ctx:OdpsParser.BuiltinFunctionStructureContext):
        pass


    # Enter a parse tree produced by OdpsParser#functionName.
    def enterFunctionName(self, ctx:OdpsParser.FunctionNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#functionName.
    def exitFunctionName(self, ctx:OdpsParser.FunctionNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#castExpression.
    def enterCastExpression(self, ctx:OdpsParser.CastExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#castExpression.
    def exitCastExpression(self, ctx:OdpsParser.CastExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#caseExpression.
    def enterCaseExpression(self, ctx:OdpsParser.CaseExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#caseExpression.
    def exitCaseExpression(self, ctx:OdpsParser.CaseExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#whenExpression.
    def enterWhenExpression(self, ctx:OdpsParser.WhenExpressionContext):
        pass

    # Exit a parse tree produced by OdpsParser#whenExpression.
    def exitWhenExpression(self, ctx:OdpsParser.WhenExpressionContext):
        pass


    # Enter a parse tree produced by OdpsParser#constant.
    def enterConstant(self, ctx:OdpsParser.ConstantContext):
        pass

    # Exit a parse tree produced by OdpsParser#constant.
    def exitConstant(self, ctx:OdpsParser.ConstantContext):
        pass


    # Enter a parse tree produced by OdpsParser#simpleStringLiteral.
    def enterSimpleStringLiteral(self, ctx:OdpsParser.SimpleStringLiteralContext):
        pass

    # Exit a parse tree produced by OdpsParser#simpleStringLiteral.
    def exitSimpleStringLiteral(self, ctx:OdpsParser.SimpleStringLiteralContext):
        pass


    # Enter a parse tree produced by OdpsParser#stringLiteral.
    def enterStringLiteral(self, ctx:OdpsParser.StringLiteralContext):
        pass

    # Exit a parse tree produced by OdpsParser#stringLiteral.
    def exitStringLiteral(self, ctx:OdpsParser.StringLiteralContext):
        pass


    # Enter a parse tree produced by OdpsParser#doubleQuoteStringLiteral.
    def enterDoubleQuoteStringLiteral(self, ctx:OdpsParser.DoubleQuoteStringLiteralContext):
        pass

    # Exit a parse tree produced by OdpsParser#doubleQuoteStringLiteral.
    def exitDoubleQuoteStringLiteral(self, ctx:OdpsParser.DoubleQuoteStringLiteralContext):
        pass


    # Enter a parse tree produced by OdpsParser#charSetStringLiteral.
    def enterCharSetStringLiteral(self, ctx:OdpsParser.CharSetStringLiteralContext):
        pass

    # Exit a parse tree produced by OdpsParser#charSetStringLiteral.
    def exitCharSetStringLiteral(self, ctx:OdpsParser.CharSetStringLiteralContext):
        pass


    # Enter a parse tree produced by OdpsParser#dateLiteral.
    def enterDateLiteral(self, ctx:OdpsParser.DateLiteralContext):
        pass

    # Exit a parse tree produced by OdpsParser#dateLiteral.
    def exitDateLiteral(self, ctx:OdpsParser.DateLiteralContext):
        pass


    # Enter a parse tree produced by OdpsParser#dateTimeLiteral.
    def enterDateTimeLiteral(self, ctx:OdpsParser.DateTimeLiteralContext):
        pass

    # Exit a parse tree produced by OdpsParser#dateTimeLiteral.
    def exitDateTimeLiteral(self, ctx:OdpsParser.DateTimeLiteralContext):
        pass


    # Enter a parse tree produced by OdpsParser#timestampLiteral.
    def enterTimestampLiteral(self, ctx:OdpsParser.TimestampLiteralContext):
        pass

    # Exit a parse tree produced by OdpsParser#timestampLiteral.
    def exitTimestampLiteral(self, ctx:OdpsParser.TimestampLiteralContext):
        pass


    # Enter a parse tree produced by OdpsParser#intervalLiteral.
    def enterIntervalLiteral(self, ctx:OdpsParser.IntervalLiteralContext):
        pass

    # Exit a parse tree produced by OdpsParser#intervalLiteral.
    def exitIntervalLiteral(self, ctx:OdpsParser.IntervalLiteralContext):
        pass


    # Enter a parse tree produced by OdpsParser#intervalQualifiers.
    def enterIntervalQualifiers(self, ctx:OdpsParser.IntervalQualifiersContext):
        pass

    # Exit a parse tree produced by OdpsParser#intervalQualifiers.
    def exitIntervalQualifiers(self, ctx:OdpsParser.IntervalQualifiersContext):
        pass


    # Enter a parse tree produced by OdpsParser#intervalQualifiersUnit.
    def enterIntervalQualifiersUnit(self, ctx:OdpsParser.IntervalQualifiersUnitContext):
        pass

    # Exit a parse tree produced by OdpsParser#intervalQualifiersUnit.
    def exitIntervalQualifiersUnit(self, ctx:OdpsParser.IntervalQualifiersUnitContext):
        pass


    # Enter a parse tree produced by OdpsParser#intervalQualifierPrecision.
    def enterIntervalQualifierPrecision(self, ctx:OdpsParser.IntervalQualifierPrecisionContext):
        pass

    # Exit a parse tree produced by OdpsParser#intervalQualifierPrecision.
    def exitIntervalQualifierPrecision(self, ctx:OdpsParser.IntervalQualifierPrecisionContext):
        pass


    # Enter a parse tree produced by OdpsParser#booleanValue.
    def enterBooleanValue(self, ctx:OdpsParser.BooleanValueContext):
        pass

    # Exit a parse tree produced by OdpsParser#booleanValue.
    def exitBooleanValue(self, ctx:OdpsParser.BooleanValueContext):
        pass


    # Enter a parse tree produced by OdpsParser#tableOrPartition.
    def enterTableOrPartition(self, ctx:OdpsParser.TableOrPartitionContext):
        pass

    # Exit a parse tree produced by OdpsParser#tableOrPartition.
    def exitTableOrPartition(self, ctx:OdpsParser.TableOrPartitionContext):
        pass


    # Enter a parse tree produced by OdpsParser#partitionSpec.
    def enterPartitionSpec(self, ctx:OdpsParser.PartitionSpecContext):
        pass

    # Exit a parse tree produced by OdpsParser#partitionSpec.
    def exitPartitionSpec(self, ctx:OdpsParser.PartitionSpecContext):
        pass


    # Enter a parse tree produced by OdpsParser#partitionVal.
    def enterPartitionVal(self, ctx:OdpsParser.PartitionValContext):
        pass

    # Exit a parse tree produced by OdpsParser#partitionVal.
    def exitPartitionVal(self, ctx:OdpsParser.PartitionValContext):
        pass


    # Enter a parse tree produced by OdpsParser#dateWithoutQuote.
    def enterDateWithoutQuote(self, ctx:OdpsParser.DateWithoutQuoteContext):
        pass

    # Exit a parse tree produced by OdpsParser#dateWithoutQuote.
    def exitDateWithoutQuote(self, ctx:OdpsParser.DateWithoutQuoteContext):
        pass


    # Enter a parse tree produced by OdpsParser#dropPartitionSpec.
    def enterDropPartitionSpec(self, ctx:OdpsParser.DropPartitionSpecContext):
        pass

    # Exit a parse tree produced by OdpsParser#dropPartitionSpec.
    def exitDropPartitionSpec(self, ctx:OdpsParser.DropPartitionSpecContext):
        pass


    # Enter a parse tree produced by OdpsParser#sysFuncNames.
    def enterSysFuncNames(self, ctx:OdpsParser.SysFuncNamesContext):
        pass

    # Exit a parse tree produced by OdpsParser#sysFuncNames.
    def exitSysFuncNames(self, ctx:OdpsParser.SysFuncNamesContext):
        pass


    # Enter a parse tree produced by OdpsParser#descFuncNames.
    def enterDescFuncNames(self, ctx:OdpsParser.DescFuncNamesContext):
        pass

    # Exit a parse tree produced by OdpsParser#descFuncNames.
    def exitDescFuncNames(self, ctx:OdpsParser.DescFuncNamesContext):
        pass


    # Enter a parse tree produced by OdpsParser#functionIdentifier.
    def enterFunctionIdentifier(self, ctx:OdpsParser.FunctionIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#functionIdentifier.
    def exitFunctionIdentifier(self, ctx:OdpsParser.FunctionIdentifierContext):
        pass


    # Enter a parse tree produced by OdpsParser#reserved.
    def enterReserved(self, ctx:OdpsParser.ReservedContext):
        pass

    # Exit a parse tree produced by OdpsParser#reserved.
    def exitReserved(self, ctx:OdpsParser.ReservedContext):
        pass


    # Enter a parse tree produced by OdpsParser#nonReserved.
    def enterNonReserved(self, ctx:OdpsParser.NonReservedContext):
        pass

    # Exit a parse tree produced by OdpsParser#nonReserved.
    def exitNonReserved(self, ctx:OdpsParser.NonReservedContext):
        pass


    # Enter a parse tree produced by OdpsParser#sql11ReservedKeywordsUsedAsCastFunctionName.
    def enterSql11ReservedKeywordsUsedAsCastFunctionName(self, ctx:OdpsParser.Sql11ReservedKeywordsUsedAsCastFunctionNameContext):
        pass

    # Exit a parse tree produced by OdpsParser#sql11ReservedKeywordsUsedAsCastFunctionName.
    def exitSql11ReservedKeywordsUsedAsCastFunctionName(self, ctx:OdpsParser.Sql11ReservedKeywordsUsedAsCastFunctionNameContext):
        pass


    # Enter a parse tree produced by OdpsParser#sql11ReservedKeywordsUsedAsIdentifier.
    def enterSql11ReservedKeywordsUsedAsIdentifier(self, ctx:OdpsParser.Sql11ReservedKeywordsUsedAsIdentifierContext):
        pass

    # Exit a parse tree produced by OdpsParser#sql11ReservedKeywordsUsedAsIdentifier.
    def exitSql11ReservedKeywordsUsedAsIdentifier(self, ctx:OdpsParser.Sql11ReservedKeywordsUsedAsIdentifierContext):
        pass



del OdpsParser