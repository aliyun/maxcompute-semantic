# Generated from /var/folders/29/8kxv8jjj1vsc7tghdf32hnp80000gp/T/tmp.kTf3n8txnp/OdpsParser.g4 by ANTLR 4.13.2
from antlr4 import *
if "." in __name__:
    from .OdpsParser import OdpsParser
else:
    from OdpsParser import OdpsParser

# This class defines a complete generic visitor for a parse tree produced by OdpsParser.

class OdpsParserVisitor(ParseTreeVisitor):

    # Visit a parse tree produced by OdpsParser#script.
    def visitScript(self, ctx:OdpsParser.ScriptContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#userCodeBlock.
    def visitUserCodeBlock(self, ctx:OdpsParser.UserCodeBlockContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#statement.
    def visitStatement(self, ctx:OdpsParser.StatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#compoundStatement.
    def visitCompoundStatement(self, ctx:OdpsParser.CompoundStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#emptyStatement.
    def visitEmptyStatement(self, ctx:OdpsParser.EmptyStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#execStatement.
    def visitExecStatement(self, ctx:OdpsParser.ExecStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#cteStatement.
    def visitCteStatement(self, ctx:OdpsParser.CteStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableAliasWithCols.
    def visitTableAliasWithCols(self, ctx:OdpsParser.TableAliasWithColsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#subQuerySource.
    def visitSubQuerySource(self, ctx:OdpsParser.SubQuerySourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#explainStatement.
    def visitExplainStatement(self, ctx:OdpsParser.ExplainStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#ifStatement.
    def visitIfStatement(self, ctx:OdpsParser.IfStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#loopStatement.
    def visitLoopStatement(self, ctx:OdpsParser.LoopStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#functionDefinition.
    def visitFunctionDefinition(self, ctx:OdpsParser.FunctionDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#functionParameters.
    def visitFunctionParameters(self, ctx:OdpsParser.FunctionParametersContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#parameterDefinition.
    def visitParameterDefinition(self, ctx:OdpsParser.ParameterDefinitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#typeDeclaration.
    def visitTypeDeclaration(self, ctx:OdpsParser.TypeDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#parameterTypeDeclaration.
    def visitParameterTypeDeclaration(self, ctx:OdpsParser.ParameterTypeDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#functionTypeDeclaration.
    def visitFunctionTypeDeclaration(self, ctx:OdpsParser.FunctionTypeDeclarationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#parameterTypeDeclarationList.
    def visitParameterTypeDeclarationList(self, ctx:OdpsParser.ParameterTypeDeclarationListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#parameterColumnNameTypeList.
    def visitParameterColumnNameTypeList(self, ctx:OdpsParser.ParameterColumnNameTypeListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#parameterColumnNameType.
    def visitParameterColumnNameType(self, ctx:OdpsParser.ParameterColumnNameTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#varSizeParam.
    def visitVarSizeParam(self, ctx:OdpsParser.VarSizeParamContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#assignStatement.
    def visitAssignStatement(self, ctx:OdpsParser.AssignStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#preSelectClauses.
    def visitPreSelectClauses(self, ctx:OdpsParser.PreSelectClausesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#postSelectClauses.
    def visitPostSelectClauses(self, ctx:OdpsParser.PostSelectClausesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#selectRest.
    def visitSelectRest(self, ctx:OdpsParser.SelectRestContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#multiInsertFromRest.
    def visitMultiInsertFromRest(self, ctx:OdpsParser.MultiInsertFromRestContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#fromRest.
    def visitFromRest(self, ctx:OdpsParser.FromRestContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#simpleQueryExpression.
    def visitSimpleQueryExpression(self, ctx:OdpsParser.SimpleQueryExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#selectQueryExpression.
    def visitSelectQueryExpression(self, ctx:OdpsParser.SelectQueryExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#fromQueryExpression.
    def visitFromQueryExpression(self, ctx:OdpsParser.FromQueryExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#setOperationFactor.
    def visitSetOperationFactor(self, ctx:OdpsParser.SetOperationFactorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#queryExpression.
    def visitQueryExpression(self, ctx:OdpsParser.QueryExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#queryExpressionWithCTE.
    def visitQueryExpressionWithCTE(self, ctx:OdpsParser.QueryExpressionWithCTEContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#setRHS.
    def visitSetRHS(self, ctx:OdpsParser.SetRHSContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#multiInsertSetOperationFactor.
    def visitMultiInsertSetOperationFactor(self, ctx:OdpsParser.MultiInsertSetOperationFactorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#multiInsertSelect.
    def visitMultiInsertSelect(self, ctx:OdpsParser.MultiInsertSelectContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#multiInsertSetRHS.
    def visitMultiInsertSetRHS(self, ctx:OdpsParser.MultiInsertSetRHSContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#multiInsertBranch.
    def visitMultiInsertBranch(self, ctx:OdpsParser.MultiInsertBranchContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#fromStatement.
    def visitFromStatement(self, ctx:OdpsParser.FromStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#insertStatement.
    def visitInsertStatement(self, ctx:OdpsParser.InsertStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#selectQueryStatement.
    def visitSelectQueryStatement(self, ctx:OdpsParser.SelectQueryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#queryStatement.
    def visitQueryStatement(self, ctx:OdpsParser.QueryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#insertStatementWithCTE.
    def visitInsertStatementWithCTE(self, ctx:OdpsParser.InsertStatementWithCTEContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#subQueryExpression.
    def visitSubQueryExpression(self, ctx:OdpsParser.SubQueryExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#limitClause.
    def visitLimitClause(self, ctx:OdpsParser.LimitClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#fromSource.
    def visitFromSource(self, ctx:OdpsParser.FromSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableVariableSource.
    def visitTableVariableSource(self, ctx:OdpsParser.TableVariableSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableFunctionSource.
    def visitTableFunctionSource(self, ctx:OdpsParser.TableFunctionSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createMachineLearningModelStatment.
    def visitCreateMachineLearningModelStatment(self, ctx:OdpsParser.CreateMachineLearningModelStatmentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#variableName.
    def visitVariableName(self, ctx:OdpsParser.VariableNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#atomExpression.
    def visitAtomExpression(self, ctx:OdpsParser.AtomExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#variableRef.
    def visitVariableRef(self, ctx:OdpsParser.VariableRefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#variableCall.
    def visitVariableCall(self, ctx:OdpsParser.VariableCallContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#funNameRef.
    def visitFunNameRef(self, ctx:OdpsParser.FunNameRefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#lambdaExpression.
    def visitLambdaExpression(self, ctx:OdpsParser.LambdaExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#lambdaParameter.
    def visitLambdaParameter(self, ctx:OdpsParser.LambdaParameterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableOrColumnRef.
    def visitTableOrColumnRef(self, ctx:OdpsParser.TableOrColumnRefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#newExpression.
    def visitNewExpression(self, ctx:OdpsParser.NewExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#existsExpression.
    def visitExistsExpression(self, ctx:OdpsParser.ExistsExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#scalarSubQueryExpression.
    def visitScalarSubQueryExpression(self, ctx:OdpsParser.ScalarSubQueryExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#classNameWithPackage.
    def visitClassNameWithPackage(self, ctx:OdpsParser.ClassNameWithPackageContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#classNameOrArrayDecl.
    def visitClassNameOrArrayDecl(self, ctx:OdpsParser.ClassNameOrArrayDeclContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#classNameList.
    def visitClassNameList(self, ctx:OdpsParser.ClassNameListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#odpsqlNonReserved.
    def visitOdpsqlNonReserved(self, ctx:OdpsParser.OdpsqlNonReservedContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#relaxedKeywords.
    def visitRelaxedKeywords(self, ctx:OdpsParser.RelaxedKeywordsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#allIdentifiers.
    def visitAllIdentifiers(self, ctx:OdpsParser.AllIdentifiersContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#identifier.
    def visitIdentifier(self, ctx:OdpsParser.IdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#aliasIdentifier.
    def visitAliasIdentifier(self, ctx:OdpsParser.AliasIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#identifierWithoutSql11.
    def visitIdentifierWithoutSql11(self, ctx:OdpsParser.IdentifierWithoutSql11Context):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterTableChangeOwner.
    def visitAlterTableChangeOwner(self, ctx:OdpsParser.AlterTableChangeOwnerContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterViewChangeOwner.
    def visitAlterViewChangeOwner(self, ctx:OdpsParser.AlterViewChangeOwnerContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterTableEnableHubTable.
    def visitAlterTableEnableHubTable(self, ctx:OdpsParser.AlterTableEnableHubTableContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableLifecycle.
    def visitTableLifecycle(self, ctx:OdpsParser.TableLifecycleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#setStatement.
    def visitSetStatement(self, ctx:OdpsParser.SetStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#anythingButEqualOrSemi.
    def visitAnythingButEqualOrSemi(self, ctx:OdpsParser.AnythingButEqualOrSemiContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#anythingButSemi.
    def visitAnythingButSemi(self, ctx:OdpsParser.AnythingButSemiContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#setProjectStatement.
    def visitSetProjectStatement(self, ctx:OdpsParser.SetProjectStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#label.
    def visitLabel(self, ctx:OdpsParser.LabelContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewInfoVal.
    def visitSkewInfoVal(self, ctx:OdpsParser.SkewInfoValContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#memberAccessOperator.
    def visitMemberAccessOperator(self, ctx:OdpsParser.MemberAccessOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#methodAccessOperator.
    def visitMethodAccessOperator(self, ctx:OdpsParser.MethodAccessOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#isNullOperator.
    def visitIsNullOperator(self, ctx:OdpsParser.IsNullOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#inOperator.
    def visitInOperator(self, ctx:OdpsParser.InOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#betweenOperator.
    def visitBetweenOperator(self, ctx:OdpsParser.BetweenOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mathExpression.
    def visitMathExpression(self, ctx:OdpsParser.MathExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#unarySuffixExpression.
    def visitUnarySuffixExpression(self, ctx:OdpsParser.UnarySuffixExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#unaryPrefixExpression.
    def visitUnaryPrefixExpression(self, ctx:OdpsParser.UnaryPrefixExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#fieldExpression.
    def visitFieldExpression(self, ctx:OdpsParser.FieldExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#logicalExpression.
    def visitLogicalExpression(self, ctx:OdpsParser.LogicalExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#notExpression.
    def visitNotExpression(self, ctx:OdpsParser.NotExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#equalExpression.
    def visitEqualExpression(self, ctx:OdpsParser.EqualExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mathExpressionListInParentheses.
    def visitMathExpressionListInParentheses(self, ctx:OdpsParser.MathExpressionListInParenthesesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mathExpressionList.
    def visitMathExpressionList(self, ctx:OdpsParser.MathExpressionListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#expression.
    def visitExpression(self, ctx:OdpsParser.ExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#statisticStatement.
    def visitStatisticStatement(self, ctx:OdpsParser.StatisticStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#addRemoveStatisticStatement.
    def visitAddRemoveStatisticStatement(self, ctx:OdpsParser.AddRemoveStatisticStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#statisticInfo.
    def visitStatisticInfo(self, ctx:OdpsParser.StatisticInfoContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showStatisticStatement.
    def visitShowStatisticStatement(self, ctx:OdpsParser.ShowStatisticStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showStatisticListStatement.
    def visitShowStatisticListStatement(self, ctx:OdpsParser.ShowStatisticListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#countTableStatement.
    def visitCountTableStatement(self, ctx:OdpsParser.CountTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#statisticName.
    def visitStatisticName(self, ctx:OdpsParser.StatisticNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#instanceManagement.
    def visitInstanceManagement(self, ctx:OdpsParser.InstanceManagementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#instanceStatus.
    def visitInstanceStatus(self, ctx:OdpsParser.InstanceStatusContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#killInstance.
    def visitKillInstance(self, ctx:OdpsParser.KillInstanceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#instanceId.
    def visitInstanceId(self, ctx:OdpsParser.InstanceIdContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#resourceManagement.
    def visitResourceManagement(self, ctx:OdpsParser.ResourceManagementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#addResource.
    def visitAddResource(self, ctx:OdpsParser.AddResourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropResource.
    def visitDropResource(self, ctx:OdpsParser.DropResourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#resourceId.
    def visitResourceId(self, ctx:OdpsParser.ResourceIdContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropOfflineModel.
    def visitDropOfflineModel(self, ctx:OdpsParser.DropOfflineModelContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#getResource.
    def visitGetResource(self, ctx:OdpsParser.GetResourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#options.
    def visitOptions(self, ctx:OdpsParser.OptionsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#authorizationStatement.
    def visitAuthorizationStatement(self, ctx:OdpsParser.AuthorizationStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#listUsers.
    def visitListUsers(self, ctx:OdpsParser.ListUsersContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#listGroups.
    def visitListGroups(self, ctx:OdpsParser.ListGroupsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#addUserStatement.
    def visitAddUserStatement(self, ctx:OdpsParser.AddUserStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#addGroupStatement.
    def visitAddGroupStatement(self, ctx:OdpsParser.AddGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#removeUserStatement.
    def visitRemoveUserStatement(self, ctx:OdpsParser.RemoveUserStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#removeGroupStatement.
    def visitRemoveGroupStatement(self, ctx:OdpsParser.RemoveGroupStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#addAccountProvider.
    def visitAddAccountProvider(self, ctx:OdpsParser.AddAccountProviderContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#removeAccountProvider.
    def visitRemoveAccountProvider(self, ctx:OdpsParser.RemoveAccountProviderContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showAcl.
    def visitShowAcl(self, ctx:OdpsParser.ShowAclContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#listRoles.
    def visitListRoles(self, ctx:OdpsParser.ListRolesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#whoami.
    def visitWhoami(self, ctx:OdpsParser.WhoamiContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#listTrustedProjects.
    def visitListTrustedProjects(self, ctx:OdpsParser.ListTrustedProjectsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#addTrustedProject.
    def visitAddTrustedProject(self, ctx:OdpsParser.AddTrustedProjectContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#removeTrustedProject.
    def visitRemoveTrustedProject(self, ctx:OdpsParser.RemoveTrustedProjectContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showSecurityConfiguration.
    def visitShowSecurityConfiguration(self, ctx:OdpsParser.ShowSecurityConfigurationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showPackages.
    def visitShowPackages(self, ctx:OdpsParser.ShowPackagesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showItems.
    def visitShowItems(self, ctx:OdpsParser.ShowItemsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#installPackage.
    def visitInstallPackage(self, ctx:OdpsParser.InstallPackageContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#uninstallPackage.
    def visitUninstallPackage(self, ctx:OdpsParser.UninstallPackageContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createPackage.
    def visitCreatePackage(self, ctx:OdpsParser.CreatePackageContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#deletePackage.
    def visitDeletePackage(self, ctx:OdpsParser.DeletePackageContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#addToPackage.
    def visitAddToPackage(self, ctx:OdpsParser.AddToPackageContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#removeFromPackage.
    def visitRemoveFromPackage(self, ctx:OdpsParser.RemoveFromPackageContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#allowPackage.
    def visitAllowPackage(self, ctx:OdpsParser.AllowPackageContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#disallowPackage.
    def visitDisallowPackage(self, ctx:OdpsParser.DisallowPackageContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#putPolicy.
    def visitPutPolicy(self, ctx:OdpsParser.PutPolicyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#getPolicy.
    def visitGetPolicy(self, ctx:OdpsParser.GetPolicyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#clearExpiredGrants.
    def visitClearExpiredGrants(self, ctx:OdpsParser.ClearExpiredGrantsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#grantLabel.
    def visitGrantLabel(self, ctx:OdpsParser.GrantLabelContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#revokeLabel.
    def visitRevokeLabel(self, ctx:OdpsParser.RevokeLabelContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showLabel.
    def visitShowLabel(self, ctx:OdpsParser.ShowLabelContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#grantSuperPrivilege.
    def visitGrantSuperPrivilege(self, ctx:OdpsParser.GrantSuperPrivilegeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#revokeSuperPrivilege.
    def visitRevokeSuperPrivilege(self, ctx:OdpsParser.RevokeSuperPrivilegeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createRoleStatement.
    def visitCreateRoleStatement(self, ctx:OdpsParser.CreateRoleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropRoleStatement.
    def visitDropRoleStatement(self, ctx:OdpsParser.DropRoleStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#addRoleToProject.
    def visitAddRoleToProject(self, ctx:OdpsParser.AddRoleToProjectContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#removeRoleFromProject.
    def visitRemoveRoleFromProject(self, ctx:OdpsParser.RemoveRoleFromProjectContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#grantRole.
    def visitGrantRole(self, ctx:OdpsParser.GrantRoleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#revokeRole.
    def visitRevokeRole(self, ctx:OdpsParser.RevokeRoleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#grantPrivileges.
    def visitGrantPrivileges(self, ctx:OdpsParser.GrantPrivilegesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#privilegeProperties.
    def visitPrivilegeProperties(self, ctx:OdpsParser.PrivilegePropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#privilegePropertieKeys.
    def visitPrivilegePropertieKeys(self, ctx:OdpsParser.PrivilegePropertieKeysContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#revokePrivileges.
    def visitRevokePrivileges(self, ctx:OdpsParser.RevokePrivilegesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#purgePrivileges.
    def visitPurgePrivileges(self, ctx:OdpsParser.PurgePrivilegesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showGrants.
    def visitShowGrants(self, ctx:OdpsParser.ShowGrantsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showRoleGrants.
    def visitShowRoleGrants(self, ctx:OdpsParser.ShowRoleGrantsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showRoles.
    def visitShowRoles(self, ctx:OdpsParser.ShowRolesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showRolePrincipals.
    def visitShowRolePrincipals(self, ctx:OdpsParser.ShowRolePrincipalsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#user.
    def visitUser(self, ctx:OdpsParser.UserContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#userRoleComments.
    def visitUserRoleComments(self, ctx:OdpsParser.UserRoleCommentsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#accountProvider.
    def visitAccountProvider(self, ctx:OdpsParser.AccountProviderContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#projectName.
    def visitProjectName(self, ctx:OdpsParser.ProjectNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#privilegeObjectName.
    def visitPrivilegeObjectName(self, ctx:OdpsParser.PrivilegeObjectNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#privilegeObjectType.
    def visitPrivilegeObjectType(self, ctx:OdpsParser.PrivilegeObjectTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#roleName.
    def visitRoleName(self, ctx:OdpsParser.RoleNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#packageName.
    def visitPackageName(self, ctx:OdpsParser.PackageNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#packageNameWithProject.
    def visitPackageNameWithProject(self, ctx:OdpsParser.PackageNameWithProjectContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#principalSpecification.
    def visitPrincipalSpecification(self, ctx:OdpsParser.PrincipalSpecificationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#principalName.
    def visitPrincipalName(self, ctx:OdpsParser.PrincipalNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#principalIdentifier.
    def visitPrincipalIdentifier(self, ctx:OdpsParser.PrincipalIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#privilege.
    def visitPrivilege(self, ctx:OdpsParser.PrivilegeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#privilegeType.
    def visitPrivilegeType(self, ctx:OdpsParser.PrivilegeTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#privilegeObject.
    def visitPrivilegeObject(self, ctx:OdpsParser.PrivilegeObjectContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#filePath.
    def visitFilePath(self, ctx:OdpsParser.FilePathContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#policyCondition.
    def visitPolicyCondition(self, ctx:OdpsParser.PolicyConditionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#policyConditionOp.
    def visitPolicyConditionOp(self, ctx:OdpsParser.PolicyConditionOpContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#policyKey.
    def visitPolicyKey(self, ctx:OdpsParser.PolicyKeyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#policyValue.
    def visitPolicyValue(self, ctx:OdpsParser.PolicyValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showCurrentRole.
    def visitShowCurrentRole(self, ctx:OdpsParser.ShowCurrentRoleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#setRole.
    def visitSetRole(self, ctx:OdpsParser.SetRoleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#adminOptionFor.
    def visitAdminOptionFor(self, ctx:OdpsParser.AdminOptionForContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#withAdminOption.
    def visitWithAdminOption(self, ctx:OdpsParser.WithAdminOptionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#withGrantOption.
    def visitWithGrantOption(self, ctx:OdpsParser.WithGrantOptionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#grantOptionFor.
    def visitGrantOptionFor(self, ctx:OdpsParser.GrantOptionForContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#explainOption.
    def visitExplainOption(self, ctx:OdpsParser.ExplainOptionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#loadStatement.
    def visitLoadStatement(self, ctx:OdpsParser.LoadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#replicationClause.
    def visitReplicationClause(self, ctx:OdpsParser.ReplicationClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#exportStatement.
    def visitExportStatement(self, ctx:OdpsParser.ExportStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#importStatement.
    def visitImportStatement(self, ctx:OdpsParser.ImportStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#readStatement.
    def visitReadStatement(self, ctx:OdpsParser.ReadStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#undoStatement.
    def visitUndoStatement(self, ctx:OdpsParser.UndoStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#redoStatement.
    def visitRedoStatement(self, ctx:OdpsParser.RedoStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#purgeStatement.
    def visitPurgeStatement(self, ctx:OdpsParser.PurgeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropTableVairableStatement.
    def visitDropTableVairableStatement(self, ctx:OdpsParser.DropTableVairableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#msckRepairTableStatement.
    def visitMsckRepairTableStatement(self, ctx:OdpsParser.MsckRepairTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#ddlStatement.
    def visitDdlStatement(self, ctx:OdpsParser.DdlStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partitionSpecOrPartitionId.
    def visitPartitionSpecOrPartitionId(self, ctx:OdpsParser.PartitionSpecOrPartitionIdContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableOrTableId.
    def visitTableOrTableId(self, ctx:OdpsParser.TableOrTableIdContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableHistoryStatement.
    def visitTableHistoryStatement(self, ctx:OdpsParser.TableHistoryStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#setExstore.
    def visitSetExstore(self, ctx:OdpsParser.SetExstoreContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#ifExists.
    def visitIfExists(self, ctx:OdpsParser.IfExistsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#restrictOrCascade.
    def visitRestrictOrCascade(self, ctx:OdpsParser.RestrictOrCascadeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#ifNotExists.
    def visitIfNotExists(self, ctx:OdpsParser.IfNotExistsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#rewriteEnabled.
    def visitRewriteEnabled(self, ctx:OdpsParser.RewriteEnabledContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#rewriteDisabled.
    def visitRewriteDisabled(self, ctx:OdpsParser.RewriteDisabledContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#storedAsDirs.
    def visitStoredAsDirs(self, ctx:OdpsParser.StoredAsDirsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#orReplace.
    def visitOrReplace(self, ctx:OdpsParser.OrReplaceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#ignoreProtection.
    def visitIgnoreProtection(self, ctx:OdpsParser.IgnoreProtectionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createDatabaseStatement.
    def visitCreateDatabaseStatement(self, ctx:OdpsParser.CreateDatabaseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#schemaName.
    def visitSchemaName(self, ctx:OdpsParser.SchemaNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createSchemaStatement.
    def visitCreateSchemaStatement(self, ctx:OdpsParser.CreateSchemaStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dbLocation.
    def visitDbLocation(self, ctx:OdpsParser.DbLocationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dbProperties.
    def visitDbProperties(self, ctx:OdpsParser.DbPropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dbPropertiesList.
    def visitDbPropertiesList(self, ctx:OdpsParser.DbPropertiesListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#switchDatabaseStatement.
    def visitSwitchDatabaseStatement(self, ctx:OdpsParser.SwitchDatabaseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropDatabaseStatement.
    def visitDropDatabaseStatement(self, ctx:OdpsParser.DropDatabaseStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropSchemaStatement.
    def visitDropSchemaStatement(self, ctx:OdpsParser.DropSchemaStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#databaseComment.
    def visitDatabaseComment(self, ctx:OdpsParser.DatabaseCommentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dataFormatDesc.
    def visitDataFormatDesc(self, ctx:OdpsParser.DataFormatDescContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createTableStatement.
    def visitCreateTableStatement(self, ctx:OdpsParser.CreateTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#truncateTableStatement.
    def visitTruncateTableStatement(self, ctx:OdpsParser.TruncateTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createIndexStatement.
    def visitCreateIndexStatement(self, ctx:OdpsParser.CreateIndexStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#indexComment.
    def visitIndexComment(self, ctx:OdpsParser.IndexCommentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#autoRebuild.
    def visitAutoRebuild(self, ctx:OdpsParser.AutoRebuildContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#indexTblName.
    def visitIndexTblName(self, ctx:OdpsParser.IndexTblNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#indexPropertiesPrefixed.
    def visitIndexPropertiesPrefixed(self, ctx:OdpsParser.IndexPropertiesPrefixedContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#indexProperties.
    def visitIndexProperties(self, ctx:OdpsParser.IndexPropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#indexPropertiesList.
    def visitIndexPropertiesList(self, ctx:OdpsParser.IndexPropertiesListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropIndexStatement.
    def visitDropIndexStatement(self, ctx:OdpsParser.DropIndexStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropTableStatement.
    def visitDropTableStatement(self, ctx:OdpsParser.DropTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatement.
    def visitAlterStatement(self, ctx:OdpsParser.AlterStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterSchemaStatementSuffix.
    def visitAlterSchemaStatementSuffix(self, ctx:OdpsParser.AlterSchemaStatementSuffixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterTableStatementSuffix.
    def visitAlterTableStatementSuffix(self, ctx:OdpsParser.AlterTableStatementSuffixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterTableMergePartitionSuffix.
    def visitAlterTableMergePartitionSuffix(self, ctx:OdpsParser.AlterTableMergePartitionSuffixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixAddConstraint.
    def visitAlterStatementSuffixAddConstraint(self, ctx:OdpsParser.AlterStatementSuffixAddConstraintContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterTblPartitionStatementSuffix.
    def visitAlterTblPartitionStatementSuffix(self, ctx:OdpsParser.AlterTblPartitionStatementSuffixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixPartitionLifecycle.
    def visitAlterStatementSuffixPartitionLifecycle(self, ctx:OdpsParser.AlterStatementSuffixPartitionLifecycleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterTblPartitionStatementSuffixProperties.
    def visitAlterTblPartitionStatementSuffixProperties(self, ctx:OdpsParser.AlterTblPartitionStatementSuffixPropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementPartitionKeyType.
    def visitAlterStatementPartitionKeyType(self, ctx:OdpsParser.AlterStatementPartitionKeyTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterViewStatementSuffix.
    def visitAlterViewStatementSuffix(self, ctx:OdpsParser.AlterViewStatementSuffixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterMaterializedViewStatementSuffix.
    def visitAlterMaterializedViewStatementSuffix(self, ctx:OdpsParser.AlterMaterializedViewStatementSuffixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterMaterializedViewSuffixRewrite.
    def visitAlterMaterializedViewSuffixRewrite(self, ctx:OdpsParser.AlterMaterializedViewSuffixRewriteContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterMaterializedViewSuffixRebuild.
    def visitAlterMaterializedViewSuffixRebuild(self, ctx:OdpsParser.AlterMaterializedViewSuffixRebuildContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterIndexStatementSuffix.
    def visitAlterIndexStatementSuffix(self, ctx:OdpsParser.AlterIndexStatementSuffixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterDatabaseStatementSuffix.
    def visitAlterDatabaseStatementSuffix(self, ctx:OdpsParser.AlterDatabaseStatementSuffixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterDatabaseSuffixProperties.
    def visitAlterDatabaseSuffixProperties(self, ctx:OdpsParser.AlterDatabaseSuffixPropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterDatabaseSuffixSetOwner.
    def visitAlterDatabaseSuffixSetOwner(self, ctx:OdpsParser.AlterDatabaseSuffixSetOwnerContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixRename.
    def visitAlterStatementSuffixRename(self, ctx:OdpsParser.AlterStatementSuffixRenameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixAddCol.
    def visitAlterStatementSuffixAddCol(self, ctx:OdpsParser.AlterStatementSuffixAddColContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixRenameCol.
    def visitAlterStatementSuffixRenameCol(self, ctx:OdpsParser.AlterStatementSuffixRenameColContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixDropCol.
    def visitAlterStatementSuffixDropCol(self, ctx:OdpsParser.AlterStatementSuffixDropColContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixUpdateStatsCol.
    def visitAlterStatementSuffixUpdateStatsCol(self, ctx:OdpsParser.AlterStatementSuffixUpdateStatsColContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementChangeColPosition.
    def visitAlterStatementChangeColPosition(self, ctx:OdpsParser.AlterStatementChangeColPositionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixAddPartitions.
    def visitAlterStatementSuffixAddPartitions(self, ctx:OdpsParser.AlterStatementSuffixAddPartitionsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixAddPartitionsElement.
    def visitAlterStatementSuffixAddPartitionsElement(self, ctx:OdpsParser.AlterStatementSuffixAddPartitionsElementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixTouch.
    def visitAlterStatementSuffixTouch(self, ctx:OdpsParser.AlterStatementSuffixTouchContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixArchive.
    def visitAlterStatementSuffixArchive(self, ctx:OdpsParser.AlterStatementSuffixArchiveContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixUnArchive.
    def visitAlterStatementSuffixUnArchive(self, ctx:OdpsParser.AlterStatementSuffixUnArchiveContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixChangeOwner.
    def visitAlterStatementSuffixChangeOwner(self, ctx:OdpsParser.AlterStatementSuffixChangeOwnerContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partitionLocation.
    def visitPartitionLocation(self, ctx:OdpsParser.PartitionLocationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixDropPartitions.
    def visitAlterStatementSuffixDropPartitions(self, ctx:OdpsParser.AlterStatementSuffixDropPartitionsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixProperties.
    def visitAlterStatementSuffixProperties(self, ctx:OdpsParser.AlterStatementSuffixPropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterViewSuffixProperties.
    def visitAlterViewSuffixProperties(self, ctx:OdpsParser.AlterViewSuffixPropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterViewColumnCommentSuffix.
    def visitAlterViewColumnCommentSuffix(self, ctx:OdpsParser.AlterViewColumnCommentSuffixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixSerdeProperties.
    def visitAlterStatementSuffixSerdeProperties(self, ctx:OdpsParser.AlterStatementSuffixSerdePropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tablePartitionPrefix.
    def visitTablePartitionPrefix(self, ctx:OdpsParser.TablePartitionPrefixContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixFileFormat.
    def visitAlterStatementSuffixFileFormat(self, ctx:OdpsParser.AlterStatementSuffixFileFormatContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixClusterbySortby.
    def visitAlterStatementSuffixClusterbySortby(self, ctx:OdpsParser.AlterStatementSuffixClusterbySortbyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterTblPartitionStatementSuffixSkewedLocation.
    def visitAlterTblPartitionStatementSuffixSkewedLocation(self, ctx:OdpsParser.AlterTblPartitionStatementSuffixSkewedLocationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewedLocations.
    def visitSkewedLocations(self, ctx:OdpsParser.SkewedLocationsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewedLocationsList.
    def visitSkewedLocationsList(self, ctx:OdpsParser.SkewedLocationsListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewedLocationMap.
    def visitSkewedLocationMap(self, ctx:OdpsParser.SkewedLocationMapContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixLocation.
    def visitAlterStatementSuffixLocation(self, ctx:OdpsParser.AlterStatementSuffixLocationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixSkewedby.
    def visitAlterStatementSuffixSkewedby(self, ctx:OdpsParser.AlterStatementSuffixSkewedbyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixExchangePartition.
    def visitAlterStatementSuffixExchangePartition(self, ctx:OdpsParser.AlterStatementSuffixExchangePartitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixProtectMode.
    def visitAlterStatementSuffixProtectMode(self, ctx:OdpsParser.AlterStatementSuffixProtectModeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixRenamePart.
    def visitAlterStatementSuffixRenamePart(self, ctx:OdpsParser.AlterStatementSuffixRenamePartContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixStatsPart.
    def visitAlterStatementSuffixStatsPart(self, ctx:OdpsParser.AlterStatementSuffixStatsPartContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixMergeFiles.
    def visitAlterStatementSuffixMergeFiles(self, ctx:OdpsParser.AlterStatementSuffixMergeFilesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterProtectMode.
    def visitAlterProtectMode(self, ctx:OdpsParser.AlterProtectModeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterProtectModeMode.
    def visitAlterProtectModeMode(self, ctx:OdpsParser.AlterProtectModeModeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixBucketNum.
    def visitAlterStatementSuffixBucketNum(self, ctx:OdpsParser.AlterStatementSuffixBucketNumContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#alterStatementSuffixCompact.
    def visitAlterStatementSuffixCompact(self, ctx:OdpsParser.AlterStatementSuffixCompactContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#fileFormat.
    def visitFileFormat(self, ctx:OdpsParser.FileFormatContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tabTypeExpr.
    def visitTabTypeExpr(self, ctx:OdpsParser.TabTypeExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partTypeExpr.
    def visitPartTypeExpr(self, ctx:OdpsParser.PartTypeExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#descStatement.
    def visitDescStatement(self, ctx:OdpsParser.DescStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#analyzeStatement.
    def visitAnalyzeStatement(self, ctx:OdpsParser.AnalyzeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#forColumnsStatement.
    def visitForColumnsStatement(self, ctx:OdpsParser.ForColumnsStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameOrList.
    def visitColumnNameOrList(self, ctx:OdpsParser.ColumnNameOrListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showStatement.
    def visitShowStatement(self, ctx:OdpsParser.ShowStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#listStatement.
    def visitListStatement(self, ctx:OdpsParser.ListStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#bareDate.
    def visitBareDate(self, ctx:OdpsParser.BareDateContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#lockStatement.
    def visitLockStatement(self, ctx:OdpsParser.LockStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#lockDatabase.
    def visitLockDatabase(self, ctx:OdpsParser.LockDatabaseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#lockMode.
    def visitLockMode(self, ctx:OdpsParser.LockModeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#unlockStatement.
    def visitUnlockStatement(self, ctx:OdpsParser.UnlockStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#unlockDatabase.
    def visitUnlockDatabase(self, ctx:OdpsParser.UnlockDatabaseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#resourceList.
    def visitResourceList(self, ctx:OdpsParser.ResourceListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#resource.
    def visitResource(self, ctx:OdpsParser.ResourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#resourceType.
    def visitResourceType(self, ctx:OdpsParser.ResourceTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createFunctionStatement.
    def visitCreateFunctionStatement(self, ctx:OdpsParser.CreateFunctionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropFunctionStatement.
    def visitDropFunctionStatement(self, ctx:OdpsParser.DropFunctionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#reloadFunctionStatement.
    def visitReloadFunctionStatement(self, ctx:OdpsParser.ReloadFunctionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createMacroStatement.
    def visitCreateMacroStatement(self, ctx:OdpsParser.CreateMacroStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropMacroStatement.
    def visitDropMacroStatement(self, ctx:OdpsParser.DropMacroStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createSqlFunctionStatement.
    def visitCreateSqlFunctionStatement(self, ctx:OdpsParser.CreateSqlFunctionStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#cloneTableStatement.
    def visitCloneTableStatement(self, ctx:OdpsParser.CloneTableStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createViewStatement.
    def visitCreateViewStatement(self, ctx:OdpsParser.CreateViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#viewPartition.
    def visitViewPartition(self, ctx:OdpsParser.ViewPartitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropViewStatement.
    def visitDropViewStatement(self, ctx:OdpsParser.DropViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#createMaterializedViewStatement.
    def visitCreateMaterializedViewStatement(self, ctx:OdpsParser.CreateMaterializedViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropMaterializedViewStatement.
    def visitDropMaterializedViewStatement(self, ctx:OdpsParser.DropMaterializedViewStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showFunctionIdentifier.
    def visitShowFunctionIdentifier(self, ctx:OdpsParser.ShowFunctionIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#showStmtIdentifier.
    def visitShowStmtIdentifier(self, ctx:OdpsParser.ShowStmtIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableComment.
    def visitTableComment(self, ctx:OdpsParser.TableCommentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tablePartition.
    def visitTablePartition(self, ctx:OdpsParser.TablePartitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableBuckets.
    def visitTableBuckets(self, ctx:OdpsParser.TableBucketsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableShards.
    def visitTableShards(self, ctx:OdpsParser.TableShardsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableSkewed.
    def visitTableSkewed(self, ctx:OdpsParser.TableSkewedContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#rowFormat.
    def visitRowFormat(self, ctx:OdpsParser.RowFormatContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#recordReader.
    def visitRecordReader(self, ctx:OdpsParser.RecordReaderContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#recordWriter.
    def visitRecordWriter(self, ctx:OdpsParser.RecordWriterContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#rowFormatSerde.
    def visitRowFormatSerde(self, ctx:OdpsParser.RowFormatSerdeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#rowFormatDelimited.
    def visitRowFormatDelimited(self, ctx:OdpsParser.RowFormatDelimitedContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableRowFormat.
    def visitTableRowFormat(self, ctx:OdpsParser.TableRowFormatContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tablePropertiesPrefixed.
    def visitTablePropertiesPrefixed(self, ctx:OdpsParser.TablePropertiesPrefixedContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableProperties.
    def visitTableProperties(self, ctx:OdpsParser.TablePropertiesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tablePropertiesList.
    def visitTablePropertiesList(self, ctx:OdpsParser.TablePropertiesListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#keyValueProperty.
    def visitKeyValueProperty(self, ctx:OdpsParser.KeyValuePropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#userDefinedJoinPropertiesList.
    def visitUserDefinedJoinPropertiesList(self, ctx:OdpsParser.UserDefinedJoinPropertiesListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#keyPrivProperty.
    def visitKeyPrivProperty(self, ctx:OdpsParser.KeyPrivPropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#keyProperty.
    def visitKeyProperty(self, ctx:OdpsParser.KeyPropertyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableRowFormatFieldIdentifier.
    def visitTableRowFormatFieldIdentifier(self, ctx:OdpsParser.TableRowFormatFieldIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableRowFormatCollItemsIdentifier.
    def visitTableRowFormatCollItemsIdentifier(self, ctx:OdpsParser.TableRowFormatCollItemsIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableRowFormatMapKeysIdentifier.
    def visitTableRowFormatMapKeysIdentifier(self, ctx:OdpsParser.TableRowFormatMapKeysIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableRowFormatLinesIdentifier.
    def visitTableRowFormatLinesIdentifier(self, ctx:OdpsParser.TableRowFormatLinesIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableRowNullFormat.
    def visitTableRowNullFormat(self, ctx:OdpsParser.TableRowNullFormatContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableFileFormat.
    def visitTableFileFormat(self, ctx:OdpsParser.TableFileFormatContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableLocation.
    def visitTableLocation(self, ctx:OdpsParser.TableLocationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#externalTableResource.
    def visitExternalTableResource(self, ctx:OdpsParser.ExternalTableResourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#viewResource.
    def visitViewResource(self, ctx:OdpsParser.ViewResourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#outOfLineConstraints.
    def visitOutOfLineConstraints(self, ctx:OdpsParser.OutOfLineConstraintsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#enableSpec.
    def visitEnableSpec(self, ctx:OdpsParser.EnableSpecContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#validateSpec.
    def visitValidateSpec(self, ctx:OdpsParser.ValidateSpecContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#relySpec.
    def visitRelySpec(self, ctx:OdpsParser.RelySpecContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameTypeConstraintList.
    def visitColumnNameTypeConstraintList(self, ctx:OdpsParser.ColumnNameTypeConstraintListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameTypeList.
    def visitColumnNameTypeList(self, ctx:OdpsParser.ColumnNameTypeListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partitionColumnNameTypeList.
    def visitPartitionColumnNameTypeList(self, ctx:OdpsParser.PartitionColumnNameTypeListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameTypeConstraintWithPosList.
    def visitColumnNameTypeConstraintWithPosList(self, ctx:OdpsParser.ColumnNameTypeConstraintWithPosListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameColonTypeList.
    def visitColumnNameColonTypeList(self, ctx:OdpsParser.ColumnNameColonTypeListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameList.
    def visitColumnNameList(self, ctx:OdpsParser.ColumnNameListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameListInParentheses.
    def visitColumnNameListInParentheses(self, ctx:OdpsParser.ColumnNameListInParenthesesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnName.
    def visitColumnName(self, ctx:OdpsParser.ColumnNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameOrderList.
    def visitColumnNameOrderList(self, ctx:OdpsParser.ColumnNameOrderListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#clusterColumnNameOrderList.
    def visitClusterColumnNameOrderList(self, ctx:OdpsParser.ClusterColumnNameOrderListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewedValueElement.
    def visitSkewedValueElement(self, ctx:OdpsParser.SkewedValueElementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewedColumnValuePairList.
    def visitSkewedColumnValuePairList(self, ctx:OdpsParser.SkewedColumnValuePairListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewedColumnValuePair.
    def visitSkewedColumnValuePair(self, ctx:OdpsParser.SkewedColumnValuePairContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewedColumnValues.
    def visitSkewedColumnValues(self, ctx:OdpsParser.SkewedColumnValuesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewedColumnValue.
    def visitSkewedColumnValue(self, ctx:OdpsParser.SkewedColumnValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewedValueLocationElement.
    def visitSkewedValueLocationElement(self, ctx:OdpsParser.SkewedValueLocationElementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameOrder.
    def visitColumnNameOrder(self, ctx:OdpsParser.ColumnNameOrderContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameCommentList.
    def visitColumnNameCommentList(self, ctx:OdpsParser.ColumnNameCommentListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameComment.
    def visitColumnNameComment(self, ctx:OdpsParser.ColumnNameCommentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnRefOrder.
    def visitColumnRefOrder(self, ctx:OdpsParser.ColumnRefOrderContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameTypeConstraint.
    def visitColumnNameTypeConstraint(self, ctx:OdpsParser.ColumnNameTypeConstraintContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameType.
    def visitColumnNameType(self, ctx:OdpsParser.ColumnNameTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partitionColumnNameType.
    def visitPartitionColumnNameType(self, ctx:OdpsParser.PartitionColumnNameTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#multipartIdentifier.
    def visitMultipartIdentifier(self, ctx:OdpsParser.MultipartIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameTypeConstraintWithPos.
    def visitColumnNameTypeConstraintWithPos(self, ctx:OdpsParser.ColumnNameTypeConstraintWithPosContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#constraints.
    def visitConstraints(self, ctx:OdpsParser.ConstraintsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#primaryKey.
    def visitPrimaryKey(self, ctx:OdpsParser.PrimaryKeyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#nullableSpec.
    def visitNullableSpec(self, ctx:OdpsParser.NullableSpecContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#defaultValue.
    def visitDefaultValue(self, ctx:OdpsParser.DefaultValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameColonType.
    def visitColumnNameColonType(self, ctx:OdpsParser.ColumnNameColonTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#colType.
    def visitColType(self, ctx:OdpsParser.ColTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#colTypeList.
    def visitColTypeList(self, ctx:OdpsParser.ColTypeListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#anyType.
    def visitAnyType(self, ctx:OdpsParser.AnyTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#anyTypeList.
    def visitAnyTypeList(self, ctx:OdpsParser.AnyTypeListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableTypeInfo.
    def visitTableTypeInfo(self, ctx:OdpsParser.TableTypeInfoContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#type.
    def visitType(self, ctx:OdpsParser.TypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#primitiveType.
    def visitPrimitiveType(self, ctx:OdpsParser.PrimitiveTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#builtinTypeOrUdt.
    def visitBuiltinTypeOrUdt(self, ctx:OdpsParser.BuiltinTypeOrUdtContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#primitiveTypeOrUdt.
    def visitPrimitiveTypeOrUdt(self, ctx:OdpsParser.PrimitiveTypeOrUdtContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#listType.
    def visitListType(self, ctx:OdpsParser.ListTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#structType.
    def visitStructType(self, ctx:OdpsParser.StructTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mapType.
    def visitMapType(self, ctx:OdpsParser.MapTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#unionType.
    def visitUnionType(self, ctx:OdpsParser.UnionTypeContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#setOperator.
    def visitSetOperator(self, ctx:OdpsParser.SetOperatorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#withClause.
    def visitWithClause(self, ctx:OdpsParser.WithClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#insertClause.
    def visitInsertClause(self, ctx:OdpsParser.InsertClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#destination.
    def visitDestination(self, ctx:OdpsParser.DestinationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#deleteStatement.
    def visitDeleteStatement(self, ctx:OdpsParser.DeleteStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnAssignmentClause.
    def visitColumnAssignmentClause(self, ctx:OdpsParser.ColumnAssignmentClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#setColumnsClause.
    def visitSetColumnsClause(self, ctx:OdpsParser.SetColumnsClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#updateStatement.
    def visitUpdateStatement(self, ctx:OdpsParser.UpdateStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mergeStatement.
    def visitMergeStatement(self, ctx:OdpsParser.MergeStatementContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mergeTargetTable.
    def visitMergeTargetTable(self, ctx:OdpsParser.MergeTargetTableContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mergeSourceTable.
    def visitMergeSourceTable(self, ctx:OdpsParser.MergeSourceTableContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mergeAction.
    def visitMergeAction(self, ctx:OdpsParser.MergeActionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mergeValuesCaluse.
    def visitMergeValuesCaluse(self, ctx:OdpsParser.MergeValuesCaluseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mergeSetColumnsClause.
    def visitMergeSetColumnsClause(self, ctx:OdpsParser.MergeSetColumnsClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mergeColumnAssignmentClause.
    def visitMergeColumnAssignmentClause(self, ctx:OdpsParser.MergeColumnAssignmentClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#selectClause.
    def visitSelectClause(self, ctx:OdpsParser.SelectClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#selectList.
    def visitSelectList(self, ctx:OdpsParser.SelectListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#selectTrfmClause.
    def visitSelectTrfmClause(self, ctx:OdpsParser.SelectTrfmClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#hintClause.
    def visitHintClause(self, ctx:OdpsParser.HintClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#hintList.
    def visitHintList(self, ctx:OdpsParser.HintListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#hintItem.
    def visitHintItem(self, ctx:OdpsParser.HintItemContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dynamicfilterHint.
    def visitDynamicfilterHint(self, ctx:OdpsParser.DynamicfilterHintContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#mapJoinHint.
    def visitMapJoinHint(self, ctx:OdpsParser.MapJoinHintContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewJoinHint.
    def visitSkewJoinHint(self, ctx:OdpsParser.SkewJoinHintContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#selectivityHint.
    def visitSelectivityHint(self, ctx:OdpsParser.SelectivityHintContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#multipleSkewHintArgs.
    def visitMultipleSkewHintArgs(self, ctx:OdpsParser.MultipleSkewHintArgsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewJoinHintArgs.
    def visitSkewJoinHintArgs(self, ctx:OdpsParser.SkewJoinHintArgsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewColumns.
    def visitSkewColumns(self, ctx:OdpsParser.SkewColumnsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#skewJoinHintKeyValues.
    def visitSkewJoinHintKeyValues(self, ctx:OdpsParser.SkewJoinHintKeyValuesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#hintName.
    def visitHintName(self, ctx:OdpsParser.HintNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#hintArgs.
    def visitHintArgs(self, ctx:OdpsParser.HintArgsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#hintArgName.
    def visitHintArgName(self, ctx:OdpsParser.HintArgNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#selectItem.
    def visitSelectItem(self, ctx:OdpsParser.SelectItemContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#trfmClause.
    def visitTrfmClause(self, ctx:OdpsParser.TrfmClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#selectExpression.
    def visitSelectExpression(self, ctx:OdpsParser.SelectExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#selectExpressionList.
    def visitSelectExpressionList(self, ctx:OdpsParser.SelectExpressionListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#window_clause.
    def visitWindow_clause(self, ctx:OdpsParser.Window_clauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#window_defn.
    def visitWindow_defn(self, ctx:OdpsParser.Window_defnContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#window_specification.
    def visitWindow_specification(self, ctx:OdpsParser.Window_specificationContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#window_frame.
    def visitWindow_frame(self, ctx:OdpsParser.Window_frameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#frame_exclusion.
    def visitFrame_exclusion(self, ctx:OdpsParser.Frame_exclusionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#window_frame_start_boundary.
    def visitWindow_frame_start_boundary(self, ctx:OdpsParser.Window_frame_start_boundaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#window_frame_boundary.
    def visitWindow_frame_boundary(self, ctx:OdpsParser.Window_frame_boundaryContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableAllColumns.
    def visitTableAllColumns(self, ctx:OdpsParser.TableAllColumnsContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableOrColumn.
    def visitTableOrColumn(self, ctx:OdpsParser.TableOrColumnContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableAndColumnRef.
    def visitTableAndColumnRef(self, ctx:OdpsParser.TableAndColumnRefContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#expressionList.
    def visitExpressionList(self, ctx:OdpsParser.ExpressionListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#aliasList.
    def visitAliasList(self, ctx:OdpsParser.AliasListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#fromClause.
    def visitFromClause(self, ctx:OdpsParser.FromClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#joinSource.
    def visitJoinSource(self, ctx:OdpsParser.JoinSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#joinRHS.
    def visitJoinRHS(self, ctx:OdpsParser.JoinRHSContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#uniqueJoinSource.
    def visitUniqueJoinSource(self, ctx:OdpsParser.UniqueJoinSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#uniqueJoinExpr.
    def visitUniqueJoinExpr(self, ctx:OdpsParser.UniqueJoinExprContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#uniqueJoinToken.
    def visitUniqueJoinToken(self, ctx:OdpsParser.UniqueJoinTokenContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#joinToken.
    def visitJoinToken(self, ctx:OdpsParser.JoinTokenContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#lateralView.
    def visitLateralView(self, ctx:OdpsParser.LateralViewContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableAlias.
    def visitTableAlias(self, ctx:OdpsParser.TableAliasContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableBucketSample.
    def visitTableBucketSample(self, ctx:OdpsParser.TableBucketSampleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#splitSample.
    def visitSplitSample(self, ctx:OdpsParser.SplitSampleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableSample.
    def visitTableSample(self, ctx:OdpsParser.TableSampleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableSource.
    def visitTableSource(self, ctx:OdpsParser.TableSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#availableSql11KeywordsForOdpsTableAlias.
    def visitAvailableSql11KeywordsForOdpsTableAlias(self, ctx:OdpsParser.AvailableSql11KeywordsForOdpsTableAliasContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableName.
    def visitTableName(self, ctx:OdpsParser.TableNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partitioningSpec.
    def visitPartitioningSpec(self, ctx:OdpsParser.PartitioningSpecContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partitionTableFunctionSource.
    def visitPartitionTableFunctionSource(self, ctx:OdpsParser.PartitionTableFunctionSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partitionedTableFunction.
    def visitPartitionedTableFunction(self, ctx:OdpsParser.PartitionedTableFunctionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#whereClause.
    def visitWhereClause(self, ctx:OdpsParser.WhereClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#valueRowConstructor.
    def visitValueRowConstructor(self, ctx:OdpsParser.ValueRowConstructorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#valuesTableConstructor.
    def visitValuesTableConstructor(self, ctx:OdpsParser.ValuesTableConstructorContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#valuesClause.
    def visitValuesClause(self, ctx:OdpsParser.ValuesClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#virtualTableSource.
    def visitVirtualTableSource(self, ctx:OdpsParser.VirtualTableSourceContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableNameColList.
    def visitTableNameColList(self, ctx:OdpsParser.TableNameColListContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#functionTypeCubeOrRollup.
    def visitFunctionTypeCubeOrRollup(self, ctx:OdpsParser.FunctionTypeCubeOrRollupContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#groupingSetsItem.
    def visitGroupingSetsItem(self, ctx:OdpsParser.GroupingSetsItemContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#groupingSetsClause.
    def visitGroupingSetsClause(self, ctx:OdpsParser.GroupingSetsClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#groupByKey.
    def visitGroupByKey(self, ctx:OdpsParser.GroupByKeyContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#groupByClause.
    def visitGroupByClause(self, ctx:OdpsParser.GroupByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#groupingSetExpression.
    def visitGroupingSetExpression(self, ctx:OdpsParser.GroupingSetExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#groupingSetExpressionMultiple.
    def visitGroupingSetExpressionMultiple(self, ctx:OdpsParser.GroupingSetExpressionMultipleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#groupingExpressionSingle.
    def visitGroupingExpressionSingle(self, ctx:OdpsParser.GroupingExpressionSingleContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#havingClause.
    def visitHavingClause(self, ctx:OdpsParser.HavingClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#havingCondition.
    def visitHavingCondition(self, ctx:OdpsParser.HavingConditionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#expressionsInParenthese.
    def visitExpressionsInParenthese(self, ctx:OdpsParser.ExpressionsInParentheseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#expressionsNotInParenthese.
    def visitExpressionsNotInParenthese(self, ctx:OdpsParser.ExpressionsNotInParentheseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnRefOrderInParenthese.
    def visitColumnRefOrderInParenthese(self, ctx:OdpsParser.ColumnRefOrderInParentheseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnRefOrderNotInParenthese.
    def visitColumnRefOrderNotInParenthese(self, ctx:OdpsParser.ColumnRefOrderNotInParentheseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#orderByClause.
    def visitOrderByClause(self, ctx:OdpsParser.OrderByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameOrIndexInParenthese.
    def visitColumnNameOrIndexInParenthese(self, ctx:OdpsParser.ColumnNameOrIndexInParentheseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameOrIndexNotInParenthese.
    def visitColumnNameOrIndexNotInParenthese(self, ctx:OdpsParser.ColumnNameOrIndexNotInParentheseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#columnNameOrIndex.
    def visitColumnNameOrIndex(self, ctx:OdpsParser.ColumnNameOrIndexContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#zorderByClause.
    def visitZorderByClause(self, ctx:OdpsParser.ZorderByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#clusterByClause.
    def visitClusterByClause(self, ctx:OdpsParser.ClusterByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partitionByClause.
    def visitPartitionByClause(self, ctx:OdpsParser.PartitionByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#distributeByClause.
    def visitDistributeByClause(self, ctx:OdpsParser.DistributeByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#sortByClause.
    def visitSortByClause(self, ctx:OdpsParser.SortByClauseContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#function.
    def visitFunction(self, ctx:OdpsParser.FunctionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#functionArgument.
    def visitFunctionArgument(self, ctx:OdpsParser.FunctionArgumentContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#builtinFunctionStructure.
    def visitBuiltinFunctionStructure(self, ctx:OdpsParser.BuiltinFunctionStructureContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#functionName.
    def visitFunctionName(self, ctx:OdpsParser.FunctionNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#castExpression.
    def visitCastExpression(self, ctx:OdpsParser.CastExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#caseExpression.
    def visitCaseExpression(self, ctx:OdpsParser.CaseExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#whenExpression.
    def visitWhenExpression(self, ctx:OdpsParser.WhenExpressionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#constant.
    def visitConstant(self, ctx:OdpsParser.ConstantContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#simpleStringLiteral.
    def visitSimpleStringLiteral(self, ctx:OdpsParser.SimpleStringLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#stringLiteral.
    def visitStringLiteral(self, ctx:OdpsParser.StringLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#doubleQuoteStringLiteral.
    def visitDoubleQuoteStringLiteral(self, ctx:OdpsParser.DoubleQuoteStringLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#charSetStringLiteral.
    def visitCharSetStringLiteral(self, ctx:OdpsParser.CharSetStringLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dateLiteral.
    def visitDateLiteral(self, ctx:OdpsParser.DateLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dateTimeLiteral.
    def visitDateTimeLiteral(self, ctx:OdpsParser.DateTimeLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#timestampLiteral.
    def visitTimestampLiteral(self, ctx:OdpsParser.TimestampLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#intervalLiteral.
    def visitIntervalLiteral(self, ctx:OdpsParser.IntervalLiteralContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#intervalQualifiers.
    def visitIntervalQualifiers(self, ctx:OdpsParser.IntervalQualifiersContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#intervalQualifiersUnit.
    def visitIntervalQualifiersUnit(self, ctx:OdpsParser.IntervalQualifiersUnitContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#intervalQualifierPrecision.
    def visitIntervalQualifierPrecision(self, ctx:OdpsParser.IntervalQualifierPrecisionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#booleanValue.
    def visitBooleanValue(self, ctx:OdpsParser.BooleanValueContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#tableOrPartition.
    def visitTableOrPartition(self, ctx:OdpsParser.TableOrPartitionContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partitionSpec.
    def visitPartitionSpec(self, ctx:OdpsParser.PartitionSpecContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#partitionVal.
    def visitPartitionVal(self, ctx:OdpsParser.PartitionValContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dateWithoutQuote.
    def visitDateWithoutQuote(self, ctx:OdpsParser.DateWithoutQuoteContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#dropPartitionSpec.
    def visitDropPartitionSpec(self, ctx:OdpsParser.DropPartitionSpecContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#sysFuncNames.
    def visitSysFuncNames(self, ctx:OdpsParser.SysFuncNamesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#descFuncNames.
    def visitDescFuncNames(self, ctx:OdpsParser.DescFuncNamesContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#functionIdentifier.
    def visitFunctionIdentifier(self, ctx:OdpsParser.FunctionIdentifierContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#reserved.
    def visitReserved(self, ctx:OdpsParser.ReservedContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#nonReserved.
    def visitNonReserved(self, ctx:OdpsParser.NonReservedContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#sql11ReservedKeywordsUsedAsCastFunctionName.
    def visitSql11ReservedKeywordsUsedAsCastFunctionName(self, ctx:OdpsParser.Sql11ReservedKeywordsUsedAsCastFunctionNameContext):
        return self.visitChildren(ctx)


    # Visit a parse tree produced by OdpsParser#sql11ReservedKeywordsUsedAsIdentifier.
    def visitSql11ReservedKeywordsUsedAsIdentifier(self, ctx:OdpsParser.Sql11ReservedKeywordsUsedAsIdentifierContext):
        return self.visitChildren(ctx)



del OdpsParser